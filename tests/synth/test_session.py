from wl_sync.session import SessionId

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.synth.recipe import CI_RECIPE, Fault
from wl_preproc.synth.session import generate_session


def test_produces_a_complete_session_directory(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(CI_RECIPE.session_id))
    assert layout.manifest_path.exists()
    for system in CI_RECIPE.systems:
        assert layout.system_dir(system).is_dir()
        assert layout.done_marker(system).exists()


def test_manifest_matches_the_directories_present(tmp_path):
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, SessionId.parse(CI_RECIPE.session_id))
    manifest = SessionManifest.from_yaml(layout.manifest_path.read_text())
    assert set(manifest.expected_systems) == set(CI_RECIPE.systems)


def test_returns_ground_truth_covering_the_session(tmp_path):
    truth = generate_session(tmp_path, CI_RECIPE)
    assert len(truth.trials) == sum(b.n_trials for b in CI_RECIPE.blocks)
    assert len(truth.blocks) == len(CI_RECIPE.blocks)
    assert truth.barcodes


def test_missing_device_omits_its_directory(tmp_path):
    recipe = CI_RECIPE.model_copy(
        update={"systems": ("syncbox", "bcam"), "faults": (Fault.MISSING_DEVICE,)}
    )
    generate_session(tmp_path, recipe)
    layout = SessionLayout(tmp_path, SessionId.parse(recipe.session_id))
    assert not layout.system_dir("spikeglx").exists()


def test_truncated_file_fault_shortens_the_binary(tmp_path):
    clean = tmp_path / "clean"
    broken = tmp_path / "broken"
    clean.mkdir()
    broken.mkdir()
    generate_session(clean, CI_RECIPE)
    generate_session(broken, CI_RECIPE.model_copy(update={"faults": (Fault.TRUNCATED_FILE,)}))
    good = next((clean / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    bad = next((broken / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    assert bad.stat().st_size < good.stat().st_size


def test_generation_is_byte_identical_for_one_seed(tmp_path):
    """Determinism is a test, not an aspiration — a fixture that changes between
    runs makes every downstream failure ambiguous."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    generate_session(first, CI_RECIPE)
    generate_session(second, CI_RECIPE)
    a = next((first / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    b = next((second / CI_RECIPE.session_id / "spikeglx").glob("*.bin"))
    assert a.read_bytes() == b.read_bytes()


def test_every_system_declares_the_files_it_wrote(tmp_path):
    """The marker must list what is actually on disk. A marker listing nothing
    would satisfy verification trivially, which is the failure this catches."""
    from wl_preproc.contracts.done import DoneMarker

    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)

    for system in CI_RECIPE.systems:
        marker = DoneMarker.from_yaml(layout.done_marker(system).read_text())
        system_dir = layout.system_dir(system)
        on_disk = {
            str(p.relative_to(system_dir))
            for p in system_dir.rglob("*")
            if p.is_file() and p.name != "DONE"
        }
        assert {entry.path for entry in marker.files} == on_disk
        for entry in marker.files:
            assert (system_dir / entry.path).stat().st_size == entry.bytes
