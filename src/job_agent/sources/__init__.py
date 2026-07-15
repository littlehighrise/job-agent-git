from job_agent.sources.greenhouse import GreenhouseSourceAdapter
from job_agent.sources.local_json import LocalJsonSourceAdapter

ADAPTERS = {"local_json": LocalJsonSourceAdapter, "greenhouse": GreenhouseSourceAdapter}
