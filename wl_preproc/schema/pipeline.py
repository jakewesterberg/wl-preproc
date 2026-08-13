# wl_preproc/schema/pipeline.py
"""The linking module, and the only place a schema is activated.

DataJoint Elements resolve their foreign keys through a *linking module*: a
namespace that supplies the tables they reference by name. Scattering
``activate()`` calls across modules makes the dependency order implicit and
turns a mistake into an unresolved-foreign-key error at import time. Doing it
here, in order, makes the order reviewable.

**element-array-ephys is deliberately absent.** Its 14 ``longblob`` attributes
declare perfectly under DataJoint 2.x and then silently destroy every array
written to them (upstream issue #230); an activation test cannot see this and
only a round-trip can. It arrives in Phase 2, once that is fixed, and the Phase
2 precondition in spec section 5.1.1 must be satisfied first.
"""

from __future__ import annotations

from wl_preproc.schema._compat import apply_datajoint_compat

apply_datajoint_compat()

from element_animal import subject  # noqa: E402
from element_event import event, trial  # noqa: E402
from element_lab import lab  # noqa: E402
from element_lab.lab import Lab, Project, Protocol, Source, User  # noqa: E402,F401
from element_session import session_with_datetime as session  # noqa: E402

# element-session references `Experimenter`; element-lab provides `User`.
# Supplying the name here is the linking module's whole purpose.
Experimenter = User

# Names element-animal and element-session resolve against this module.
Subject = subject.Subject
Session = session.Session

_activated: set[str] = set()


def activate(prefix: str = "wlpp") -> None:
    """Activate the adopted Elements, in dependency order.

    Idempotent: activating an already-activated prefix is a no-op, so a test
    suite may call this repeatedly against one database.
    """
    global Session, Subject

    if prefix in _activated:
        return

    lab.activate(f"{prefix}lab")
    subject.activate(f"{prefix}subject", linking_module=__name__)
    Subject = subject.Subject

    session.activate(f"{prefix}session", linking_module=__name__)
    Session = session.Session

    event.activate(f"{prefix}event", linking_module=__name__)
    trial.activate(f"{prefix}trial", f"{prefix}event", linking_module=__name__)

    _activated.add(prefix)
