"""The IR *schema* version: the version of the representation format.

Not to be confused with an architecture's *revision* (a version of one architecture's content,
see core/domain/architecture): upgrading the schema never changes what an architecture says,
and a user editing an architecture never changes the schema.
"""

from typing import Final

IR_SCHEMA_VERSION: Final = 1
