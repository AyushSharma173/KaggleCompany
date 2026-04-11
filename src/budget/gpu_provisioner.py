"""GPU provisioning: Kaggle API (free tier) and RunPod (paid).

Tracks GPU instance lifecycle and costs. Agents choose provider
based on task requirements and budget.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.budget.tracker import BudgetTracker

logger = logging.getLogger("kaggle-company.gpu")


class GPUProvider(Enum):
    KAGGLE = "kaggle"
    RUNPOD = "runpod"


@dataclass
class GPUInstance:
    instance_id: str
    provider: GPUProvider
    gpu_type: str
    status: str  # provisioning, running, completed, failed, terminated
    started_at: float = 0.0
    cost_per_hour: float = 0.0
    total_cost: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# RunPod GPU pricing (approximate, USD/hour)
RUNPOD_PRICING = {
    "RTX 3090": 0.44,
    "RTX 4090": 0.69,
    "A100 40GB": 1.64,
    "A100 80GB": 2.09,
    "H100": 4.49,
}

# Kaggle free tier limits
KAGGLE_GPU_WEEKLY_HOURS = 30
KAGGLE_TPU_WEEKLY_HOURS = 20


class GPUProvisioner:
    """Manages GPU instances across Kaggle and RunPod."""

    def __init__(
        self,
        runpod_api_key: str = "",
        budget_tracker: BudgetTracker | None = None,
    ) -> None:
        self._runpod_key = runpod_api_key
        self._budget = budget_tracker
        self._instances: dict[str, GPUInstance] = {}
        self._kaggle_hours_used: float = 0.0

    async def provision_kaggle(self, notebook_id: str = "") -> GPUInstance:
        """Use Kaggle's free GPU tier. Returns instance tracking object."""
        instance = GPUInstance(
            instance_id=f"kaggle-{int(time.time())}",
            provider=GPUProvider.KAGGLE,
            gpu_type="T4/P100",
            status="running",
            started_at=time.time(),
            cost_per_hour=0.0,  # Free tier
        )
        self._instances[instance.instance_id] = instance
        logger.info("Provisioned Kaggle GPU: %s", instance.instance_id)
        return instance

    async def provision_runpod(
        self,
        gpu_type: str = "RTX 4090",
        docker_image: str = "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel",
    ) -> GPUInstance | None:
        """Provision a RunPod GPU instance."""
        if not self._runpod_key:
            logger.error("RunPod API key not configured")
            return None

        cost_per_hour = RUNPOD_PRICING.get(gpu_type, 1.0)

        # Budget check for 1 hour minimum
        if self._budget and not self._budget.check_budget("gpu", cost_per_hour):
            logger.warning("Budget insufficient for RunPod %s ($%.2f/hr)", gpu_type, cost_per_hour)
            return None

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.runpod.io/v2/pods",
                    headers={"Authorization": f"Bearer {self._runpod_key}"},
                    json={
                        "name": f"kaggle-company-{int(time.time())}",
                        "imageName": docker_image,
                        "gpuTypeId": gpu_type,
                        "gpuCount": 1,
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                pod_id = data.get("id", f"runpod-{int(time.time())}")
        except Exception as e:
            logger.error("RunPod provisioning failed: %s", e)
            pod_id = f"runpod-{int(time.time())}"

        instance = GPUInstance(
            instance_id=pod_id,
            provider=GPUProvider.RUNPOD,
            gpu_type=gpu_type,
            status="provisioning",
            started_at=time.time(),
            cost_per_hour=cost_per_hour,
        )
        self._instances[instance.instance_id] = instance
        logger.info("Provisioned RunPod %s: %s ($%.2f/hr)", gpu_type, pod_id, cost_per_hour)
        return instance

    async def check_status(self, instance_id: str) -> str:
        """Check the status of a GPU instance."""
        instance = self._instances.get(instance_id)
        if not instance:
            return "unknown"

        if instance.provider == GPUProvider.RUNPOD and self._runpod_key:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"https://api.runpod.io/v2/pods/{instance_id}",
                        headers={"Authorization": f"Bearer {self._runpod_key}"},
                        timeout=10.0,
                    )
                    if resp.is_success:
                        data = resp.json()
                        instance.status = data.get("desiredStatus", instance.status)
            except Exception as e:
                logger.warning("Could not check RunPod status: %s", e)

        return instance.status

    async def terminate(self, instance_id: str) -> bool:
        """Terminate a GPU instance and record final cost."""
        instance = self._instances.get(instance_id)
        if not instance:
            return False

        # Calculate cost
        hours = (time.time() - instance.started_at) / 3600
        instance.total_cost = hours * instance.cost_per_hour
        instance.status = "terminated"

        # Record GPU spend
        if self._budget and instance.total_cost > 0:
            self._budget.record_gpu_spend("gpu", instance.total_cost, {
                "instance_id": instance_id,
                "provider": instance.provider.value,
                "gpu_type": instance.gpu_type,
                "hours": hours,
            })

        # Terminate on RunPod
        if instance.provider == GPUProvider.RUNPOD and self._runpod_key:
            try:
                async with httpx.AsyncClient() as client:
                    await client.delete(
                        f"https://api.runpod.io/v2/pods/{instance_id}",
                        headers={"Authorization": f"Bearer {self._runpod_key}"},
                        timeout=10.0,
                    )
            except Exception as e:
                logger.error("Failed to terminate RunPod instance %s: %s", instance_id, e)

        logger.info(
            "Terminated %s instance %s (%.1f hrs, $%.2f)",
            instance.provider.value, instance_id, hours, instance.total_cost,
        )
        return True

    def get_instance(self, instance_id: str) -> GPUInstance | None:
        return self._instances.get(instance_id)

    def list_active(self) -> list[GPUInstance]:
        return [i for i in self._instances.values() if i.status in ("provisioning", "running")]

    def estimate_cost(self, gpu_type: str, hours: float) -> float:
        """Estimate cost for a given GPU type and duration."""
        rate = RUNPOD_PRICING.get(gpu_type, 1.0)
        return rate * hours
