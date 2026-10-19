"""本体服务包：对象类型/属性/关系类型三等实体的业务规则、校验器与治理门禁。"""

from __future__ import annotations

from .errors import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNameInvalidError,
    OntologyNotFoundError,
    OntologyStateInvalidError,
)
from .ontology_service import OntologyService

ontology_service = OntologyService()

__all__ = [
    "OntologyService",
    "ontology_service",
    "OntologyAlreadyExistsError",
    "OntologyInvalidError",
    "OntologyNameInvalidError",
    "OntologyNotFoundError",
    "OntologyStateInvalidError",
]
