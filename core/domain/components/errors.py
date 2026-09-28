from core.domain.errors import DomainError


class InvalidSpecification(DomainError):
    """A component specification that breaks the contract (a catalog data error, or an untrusted file).
    ``details`` = {"component": id or None, "fields": [...]}: the paths of the fields at fault."""

    code = "invalid_component_specification"
    message = "The component specification is invalid."
