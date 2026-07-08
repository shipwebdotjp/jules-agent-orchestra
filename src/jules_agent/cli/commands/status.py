from __future__ import annotations

import argparse
from ...models import State
from ...services.status_service import StatusService, StatusOptions
from ...codex import OperationError


def handle_status(args: argparse.Namespace, state: State) -> int:
    service = StatusService(state)
    options = StatusOptions(
        show_all=args.all,
        show_activities=args.show_activities,
        output_func=print,
    )
    result = service.execute(options)

    if not result.success:
        raise OperationError(result.exit_code, result.message or "Status failed")

    return 0
