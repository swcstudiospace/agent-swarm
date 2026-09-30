"""Shadow scan levels (PKG-02, 07-VALIDATION G2): the project ancestor walks, profile and agent dirs, and user
`skills.customDirectories`, with omp's loader rules (description required, no agent stem fallback)."""
import pytest

from install_helpers import _write, inst

FM = "---\nname: {name}\ndescription: mine\n---\n"  # what omp's agent and skill loaders both accept


def _agent(path, name):
    return _write(path, FM.format(name=name))


def _skill(d, name):
    return _write(d / name / "SKILL.md", FM.format(name=name))


def _shadows(ws, home, env=None):
    return {(s.kind, s.name, s.level, s.path) for s in inst.shadow_scan(ws, home, (), env or {})}


def test_ancestor_project_agents_dir_warns(tmp_path, home):
    ws = tmp_path / "a" / "b" / "ws"
    ws.mkdir(parents=True)
    agent = _agent(tmp_path / "a" / ".omp" / "agents" / "mine.md", "a01-orchestrator")
    assert _shadows(ws, home) == {("agent", "a01-orchestrator", "project", agent)}


def test_nearest_project_agents_dir_hides_ancestors(tmp_path, home):
    ws = tmp_path / "a" / "ws"
    (ws / ".omp" / "agents").mkdir(parents=True)
    _agent(tmp_path / "a" / ".omp" / "agents" / "mine.md", "a01-orchestrator")
    assert _shadows(ws, home) == set()


def test_ancestor_skills_stop_at_git_toplevel(tmp_path, home):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    ws = repo / "pkg" / "ws"
    ws.mkdir(parents=True)
    inner = _skill(repo / "pkg" / ".omp" / "skills", "a05-backend")
    top = _skill(repo / ".omp" / "skills", "a06-frontend")
    _skill(tmp_path / ".omp" / "skills", "a07-data")  # above the toplevel: omp never reads it
    assert _shadows(ws, home) == {("skill", "a05-backend", "project", inner), ("skill", "a06-frontend", "project", top)}


def test_ancestor_skills_stop_at_home(tmp_path, home):
    ws = home / "proj" / "ws"
    ws.mkdir(parents=True)
    inner = _skill(home / "proj" / ".omp" / "skills", "a05-backend")
    _skill(tmp_path / ".omp" / "skills", "a07-data")  # above $HOME
    assert _shadows(ws, home) == {("skill", "a05-backend", "project", inner)}


def test_ancestor_skills_walk_past_a_stop_dir_that_is_not_an_ancestor(tmp_path, home):
    ws = tmp_path / "a" / "ws"  # neither in git nor under $HOME: omp walks to /
    ws.mkdir(parents=True)
    skill = _skill(tmp_path / "a" / ".omp" / "skills", "a05-backend")
    assert _shadows(ws, home) == {("skill", "a05-backend", "project", skill)}


@pytest.mark.parametrize("var", ["OMP_PROFILE", "PI_PROFILE"])
def test_omp_profile_agent_and_skill_dirs_warn(tmp_path, home, var):
    ws = tmp_path / "ws"
    ws.mkdir()
    profile = home / ".omp" / "profiles" / "work" / "agent"
    agent = _agent(profile / "agents" / "mine.md", "a02-requirements")
    skill = _skill(profile / "skills", "a03-architect")
    _agent(home / ".omp" / "agent" / "agents" / "base.md", "a04-ux-designer")  # the base dir is not read under a profile
    _skill(home / ".omp" / "agent" / "skills", "a05-backend")
    assert _shadows(ws, home, {var: "work"}) == {
        ("agent", "a02-requirements", "user", agent),
        ("skill", "a03-architect", "user", skill),
    }


def test_empty_omp_profile_selects_the_default_profile(tmp_path, home):
    ws = tmp_path / "ws"
    ws.mkdir()
    base = _agent(home / ".omp" / "agent" / "agents" / "base.md", "a04-ux-designer")
    _agent(home / ".omp" / "profiles" / "work" / "agent" / "agents" / "mine.md", "a02-requirements")
    assert _shadows(ws, home, {"OMP_PROFILE": "", "PI_PROFILE": "work"}) == {("agent", "a04-ux-designer", "user", base)}


def test_pi_coding_agent_dir_skills_warn(tmp_path, home):
    ws = tmp_path / "ws"
    ws.mkdir()
    agent_dir = tmp_path / "agentdir"
    skill = _skill(agent_dir / "skills", "a05-backend")
    _skill(home / ".omp" / "agent" / "skills", "a06-frontend")
    _agent(agent_dir / "agents" / "mine.md", "a07-data")  # user agents never come from PI_CODING_AGENT_DIR
    agent = _agent(home / ".omp" / "agent" / "agents" / "base.md", "a08-qa")
    assert _shadows(ws, home, {"PI_CODING_AGENT_DIR": str(agent_dir)}) == {
        ("skill", "a05-backend", "user", skill),
        ("agent", "a08-qa", "user", agent),
    }


def test_profile_wins_over_pi_coding_agent_dir(tmp_path, home):
    ws = tmp_path / "ws"
    ws.mkdir()
    _skill(tmp_path / "agentdir" / "skills", "a05-backend")
    skill = _skill(home / ".omp" / "profiles" / "work" / "agent" / "skills", "a06-frontend")
    env = {"PI_CODING_AGENT_DIR": str(tmp_path / "agentdir"), "OMP_PROFILE": "work"}
    assert _shadows(ws, home, env) == {("skill", "a06-frontend", "user", skill)}


@pytest.mark.parametrize("where", ["config.yml", "config.yaml", "agent-dir", "profile"])
def test_user_custom_skill_directory_warns(tmp_path, home, where):
    ws = tmp_path / "ws"
    ws.mkdir()
    env = {}
    cfg_dir = home / ".omp" / "agent"
    if where == "agent-dir":
        cfg_dir = tmp_path / "agentdir"
        env = {"PI_CODING_AGENT_DIR": str(cfg_dir)}
    elif where == "profile":
        cfg_dir = home / ".omp" / "profiles" / "work" / "agent"
        env = {"OMP_PROFILE": "work"}
    name = "config.yaml" if where == "config.yaml" else "config.yml"
    _write(cfg_dir / name, "skills:\n  customDirectories:\n    - ~/custom\n")
    skill = _skill(home / "custom", "a06-frontend")
    assert _shadows(ws, home, env) == {("skill", "a06-frontend", "user", skill)}
