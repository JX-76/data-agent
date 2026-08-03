"""Auto-scaling support for Data Agent.

Provides:
- Horizontal pod autoscaler configuration
- Resource usage monitoring
- Scale triggers
"""

from __future__ import annotations

import os
from typing import Optional


class AutoScalerConfig:
    """Configuration for auto-scaling."""
    
    def __init__(self):
        # Kubernetes HPA configuration
        self.min_replicas = int(os.environ.get("DATA_AGENT_MIN_REPLICAS", "2"))
        self.max_replicas = int(os.environ.get("DATA_AGENT_MAX_REPLICAS", "10"))
        self.target_cpu_utilization = int(os.environ.get("DATA_AGENT_TARGET_CPU", "70"))
        self.target_memory_utilization = int(os.environ.get("DATA_AGENT_TARGET_MEMORY", "80"))
        
        # Scale down stabilization
        self.scale_down_delay = int(os.environ.get("DATA_AGENT_SCALE_DOWN_DELAY", "300"))
        self.scale_down_factor = float(os.environ.get("DATA_AGENT_SCALE_DOWN_FACTOR", "0.5"))
    
    def to_k8s_hpa(self) -> dict:
        """Generate Kubernetes HPA manifest."""
        return {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": "data-agent-hpa",
                "namespace": "default"
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "data-agent"
                },
                "minReplicas": self.min_replicas,
                "maxReplicas": self.max_replicas,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": self.target_cpu_utilization
                            }
                        }
                    },
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "memory",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": self.target_memory_utilization
                            }
                        }
                    }
                ],
                "behavior": {
                    "scaleDown": {
                        "stabilizationWindowSeconds": self.scale_down_delay,
                        "policies": [
                            {
                                "type": "Percent",
                                "value": int(self.scale_down_factor * 100),
                                "periodSeconds": 60
                            }
                        ]
                    }
                }
            }
        }


class ResourceMonitor:
    """Monitor resource usage for auto-scaling decisions."""
    
    def __init__(self):
        self.cpu_usage = 0.0
        self.memory_usage = 0.0
        self.request_count = 0
    
    def update(self, cpu_percent: float, memory_percent: float):
        """Update resource usage."""
        self.cpu_usage = cpu_percent
        self.memory_usage = memory_percent
    
    def should_scale_up(self, threshold: float = 80.0) -> bool:
        """Check if should scale up."""
        return self.cpu_usage > threshold or self.memory_usage > threshold
    
    def should_scale_down(self, threshold: float = 30.0) -> bool:
        """Check if should scale down."""
        return self.cpu_usage < threshold and self.memory_usage < threshold
    
    def get_metrics(self) -> dict:
        """Get current resource metrics."""
        return {
            "cpu_usage": self.cpu_usage,
            "memory_usage": self.memory_usage,
            "request_count": self.request_count,
        }


# Global instance
autoscaler_config = AutoScalerConfig()
resource_monitor = ResourceMonitor()
