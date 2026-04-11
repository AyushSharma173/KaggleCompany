"""GPU execution tools: submit jobs, check status, manage instances."""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from src.tools import AgentRole, ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from src.budget.gpu_provisioner import GPUProvisioner

logger = logging.getLogger("kaggle-company.tools.gpu")


def make_gpu_tools(gpu_provisioner: GPUProvisioner | None) -> list[ToolDefinition]:
    """Create GPU tools with bound provisioner reference."""

    async def gpu_execute(params: dict[str, Any]) -> str:
        """Submit a training job to GPU (Kaggle free tier or RunPod).

        This is NON-BLOCKING: it starts the job and returns a job ID.
        Use check_gpu_job to poll for completion.
        """
        provider = params.get("provider", "kaggle")
        gpu_type = params.get("gpu_type", "T4")
        script_path = params.get("script_path", "")
        estimated_hours = params.get("estimated_hours", 1.0)

        if not gpu_provisioner:
            return "Error: GPU provisioner not configured"

        if provider == "kaggle":
            instance = await gpu_provisioner.provision_kaggle()
            return json.dumps({
                "job_id": instance.instance_id,
                "provider": "kaggle",
                "gpu_type": instance.gpu_type,
                "status": "running",
                "cost_per_hour": 0.0,
                "note": "Free tier. Use check_gpu_job to monitor progress.",
            })
        elif provider == "runpod":
            estimated_cost = gpu_provisioner.estimate_cost(gpu_type, estimated_hours)
            instance = await gpu_provisioner.provision_runpod(gpu_type=gpu_type)
            if instance is None:
                return "Error: Failed to provision RunPod GPU (check budget and API key)"
            return json.dumps({
                "job_id": instance.instance_id,
                "provider": "runpod",
                "gpu_type": gpu_type,
                "status": "provisioning",
                "cost_per_hour": instance.cost_per_hour,
                "estimated_cost": estimated_cost,
                "note": "Instance is starting. Use check_gpu_job to monitor.",
            })
        else:
            return f"Error: unknown provider '{provider}'. Use 'kaggle' or 'runpod'."

    async def check_gpu_job(params: dict[str, Any]) -> str:
        """Check the status of a GPU job."""
        job_id = params.get("job_id", "")
        if not job_id:
            return "Error: job_id is required"
        if not gpu_provisioner:
            return "Error: GPU provisioner not configured"

        status = await gpu_provisioner.check_status(job_id)
        instance = gpu_provisioner.get_instance(job_id)

        if instance:
            import time
            hours = (time.time() - instance.started_at) / 3600
            return json.dumps({
                "job_id": job_id,
                "status": status,
                "provider": instance.provider.value,
                "gpu_type": instance.gpu_type,
                "running_hours": round(hours, 2),
                "current_cost": round(hours * instance.cost_per_hour, 2),
            })
        return json.dumps({"job_id": job_id, "status": status})

    async def terminate_gpu(params: dict[str, Any]) -> str:
        """Terminate a GPU instance to stop billing."""
        job_id = params.get("job_id", "")
        if not job_id:
            return "Error: job_id is required"
        if not gpu_provisioner:
            return "Error: GPU provisioner not configured"

        success = await gpu_provisioner.terminate(job_id)
        if success:
            instance = gpu_provisioner.get_instance(job_id)
            cost = instance.total_cost if instance else 0
            return json.dumps({
                "job_id": job_id,
                "status": "terminated",
                "total_cost": round(cost, 2),
            })
        return f"Error: could not terminate job {job_id}"

    async def list_gpu_instances(params: dict[str, Any]) -> str:
        """List all active GPU instances."""
        if not gpu_provisioner:
            return "Error: GPU provisioner not configured"

        instances = gpu_provisioner.list_active()
        return json.dumps({
            "active_instances": [
                {
                    "id": i.instance_id,
                    "provider": i.provider.value,
                    "gpu_type": i.gpu_type,
                    "status": i.status,
                    "cost_per_hour": i.cost_per_hour,
                }
                for i in instances
            ]
        }, indent=2)

    return [
        ToolDefinition(
            name="gpu_execute",
            description="Submit a training job to GPU. NON-BLOCKING: starts job, returns job_id. Use check_gpu_job to monitor.",
            input_schema={
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "description": "'kaggle' (free) or 'runpod' (paid)", "default": "kaggle"},
                    "gpu_type": {"type": "string", "description": "GPU type for RunPod (e.g., 'RTX 4090', 'A100 80GB')", "default": "RTX 4090"},
                    "script_path": {"type": "string", "description": "Path to training script in workspace"},
                    "estimated_hours": {"type": "number", "description": "Estimated runtime in hours", "default": 1.0},
                },
                "additionalProperties": False,
            },
            handler=gpu_execute,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="check_gpu_job",
            description="Check the status of a running GPU job.",
            input_schema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID from gpu_execute"},
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=check_gpu_job,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="terminate_gpu",
            description="Terminate a GPU instance to stop billing.",
            input_schema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Instance/job ID to terminate"},
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            handler=terminate_gpu,
            allowed_roles={AgentRole.WORKER, AgentRole.SUBAGENT},
        ),
        ToolDefinition(
            name="list_gpu_instances",
            description="List all active GPU instances with status and costs.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=list_gpu_instances,
            allowed_roles={AgentRole.VP, AgentRole.WORKER},
        ),
    ]


def register_gpu_tools(registry: ToolRegistry, gpu_provisioner: GPUProvisioner | None = None) -> None:
    """Register all GPU tools."""
    for tool in make_gpu_tools(gpu_provisioner):
        registry.register(tool)
