from __future__ import annotations

import argparse
from ...models import State
from ...services.status_service import StatusService, StatusOptions

def handle_status(args: argparse.Namespace, state: State) -> int:
    service = StatusService(state)
    options = StatusOptions(
        show_all=args.all,
        show_activities=args.show_activities,
        output_func=print,
    )
    result = service.execute(options)
    return result.exit_code
