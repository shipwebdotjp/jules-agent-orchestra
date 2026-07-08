from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...client import JulesClient
from ...models import State
from ...services.import_service import ImportService, ImportOptions
from ...codex import OperationError


def handle_import(
    args: argparse.Namespace,
    state: State,
    client: JulesClient,
    cwd: Path,
) -> int:
    service = ImportService(state, client, cwd)
    options = ImportOptions(
        session_id_input=args.session_id,
        output_func=print,
        error_func=lambda m: print(m, file=sys.stderr),
    )

    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Import failed")

    if result.message:
        print(result.message)

    return 0
