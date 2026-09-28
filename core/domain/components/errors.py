from core.domain.errors import DomainError


class InvalidSpecification(DomainError):
    """A component specification that breaks the contract (a catalog data error, or an untrusted file).
    ``details`` = {"component": id or None, "fields": [...]}: the paths of the fields at fault."""

    code = "invalid_component_specification"
    message = "The component specification is invalid."


class InvalidCatalog(DomainError):
    """The catalog as a whole breaks a rule: a file in the wrong place, a published version changed or
    removed, a gap in a component's versions. ``details`` = {"reason", and the file or ``ref``}."""

    code = "invalid_component_catalog"
    message = "The component catalog is invalid."


class ComponentNotFound(DomainError):
    """No catalog entry with this id (or no such version of it). ``details`` = {"component"} and,
    when a version was asked for, {"version"}."""

    code = "component_not_found"
    message = "Component specification not found."
