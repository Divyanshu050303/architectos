"""The source adapters this ArchitectOS ships, in detection order: the most specific shape first (an
Architecture IR document, Terraform JSON), then Compose, then Kubernetes manifests."""

from .adapters import SourceAdapter
from .architecture_json import ArchitectureJsonAdapter
from .compose import ComposeAdapter
from .kubernetes import KubernetesAdapter
from .terraform import TerraformJsonAdapter

ADAPTERS: tuple[SourceAdapter, ...] = (
    ArchitectureJsonAdapter(),
    TerraformJsonAdapter(),
    ComposeAdapter(),
    KubernetesAdapter(),
)
