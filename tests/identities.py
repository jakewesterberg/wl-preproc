"""Fresh identities for database-backed tests: an animal no other test names,
its sessions, probe serials and request keys.

Every module in one pytest process shares one database (`tests/conftest.py`'s
`dj_conn`), and a clash there is silent: the landing helpers insert with
`skip_duplicates`, so a second test given the same animal and date reuses the
first one's session and reads its files. Values written by hand have to be
checked unclaimed across `tests/`; these need no check.

    animal = new_animal()                    # zz000001, say
    first = animal.session()                 # 09:00 on a past day of its own
    again = animal.session(on=first.date)    # 10:00 that day, `<date>_02`
    serial = new_serial()                    # 19099000001, say
    key = animal.key("canonical")            # an idempotency key

Pass every field to the helper that lands the session, its time included:
`test_spikeglx_restart.py::_session(tmp_path_factory,
session_datetime=again.session_datetime, subject=again.subject,
session_id=again.session_id, probe_serial=serial)`. `again.key` is then its
key. `_session` given no time lands at 09:00 on the id's date, so a day's
second session would land on its first, silently (the identities review's
I1); `_land`, `_plant`, `_build_stepped_session` and `_populate_generated`
each take the time. Sessions landed through the real watcher (`scan_once`)
are not these: it dates every recipe at `SYNTH_EPOCH`.

A default day is in the past, since nwbinspector calls a future session
start critical; a test needing a particular day, past or future, passes `on`.
A day before an animal's birth date (`_land` writes 2020-01-01) gives a
negative age.

**The series are reserved:** `test_identities.py` fails on any test that
writes one of their values by hand. Each pytest process has its own database,
so a counter per process is enough. Existing tests keep their hand-picked
values (their dates carry meaning: past or future, recent or not, same-day
pairs)."""

from __future__ import annotations

import collections
import dataclasses
import datetime
import itertools

from wl_preproc.ingest.landing import SUBJECT_MAX_LEN

if __name__ != "tests.identities":
    # Under another name it would be a second module counting again from one,
    # handing out what this one already has (the identities review's M1).
    raise ImportError(f"tests/identities.py imported as {__name__}: import it as tests.identities")

PREFIX = "zz"
SERIAL_PREFIX = "19099"
# The first animal's first day: after `_land`'s birth date (2020-01-01), so
# every age is positive. Each animal starts a day later, so the default days
# reach today after about a thousand animals in one run.
FIRST_DAY = datetime.date(2024, 1, 1)

_animals = itertools.count(1)
_serials = itertools.count(1)


@dataclasses.dataclass(frozen=True)
class Session:
    subject: str
    session_id: str
    session_datetime: datetime.datetime

    @property
    def date(self) -> datetime.date:
        return self.session_datetime.date()

    @property
    def key(self) -> dict:
        """The session's key in `pipeline.Session`."""
        return {"subject": self.subject, "session_datetime": self.session_datetime}


@dataclasses.dataclass
class Animal:
    subject: str
    first_day: datetime.date
    _per_day: collections.Counter = dataclasses.field(default_factory=collections.Counter)

    def session(self, on: datetime.date | None = None) -> Session:
        """A new session: on `on`, or on the next day this animal has none.
        The first session of a day is `<date>_01` at 09:00, the next `_02` at
        10:00, up to 15."""
        if on is None:
            on = self.first_day
            while self._per_day[on]:
                on += datetime.timedelta(days=1)
            if on >= datetime.datetime.now(datetime.UTC).date():
                raise RuntimeError(f"no past day is left for {self.subject}'s next session")
        number = self._per_day[on] + 1
        if 8 + number > 23:
            raise RuntimeError(f"{on} holds 15 sessions already for {self.subject}, 09:00 to 23:00")
        self._per_day[on] = number
        return Session(subject=self.subject, session_id=f"{on.isoformat()}_{number:02d}",
                       session_datetime=datetime.datetime.combine(on, datetime.time(8 + number)))

    def key(self, name: str) -> str:
        """A request idempotency key of this animal's."""
        return f"{self.subject}-{name}"


def new_animal() -> Animal:
    """An animal no test has named: `zz` and six digits, the width
    `pipeline.subject.Subject` allows, so no name holds another."""
    number = next(_animals)
    subject = f"{PREFIX}{number:0{SUBJECT_MAX_LEN - len(PREFIX)}d}"
    if len(subject) > SUBJECT_MAX_LEN:
        raise RuntimeError(f"the animal series is used up at {subject}")
    return Animal(subject=subject, first_day=FIRST_DAY + datetime.timedelta(days=number - 1))


def new_serial() -> str:
    """A probe serial no test has written: eleven digits, as a Neuropixels
    serial is."""
    return f"{SERIAL_PREFIX}{next(_serials):06d}"
