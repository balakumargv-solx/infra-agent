"""
Basic tests to verify project structure
"""

import sys
from pathlib import Path

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_project_structure():
    """Test that basic project structure exists"""
    project_root = Path(__file__).parent.parent
    
    # Check main directories exist
    assert (project_root / "src").exists()
    assert (project_root / "src" / "models").exists()
    assert (project_root / "src" / "services").exists()
    assert (project_root / "src" / "web").exists()
    assert (project_root / "src" / "config").exists()
    assert (project_root / "tests").exists()
    
    # Check main files exist
    assert (project_root / "main.py").exists()
    assert (project_root / "requirements.txt").exists()
    assert (project_root / "README.md").exists()
    assert (project_root / "pyproject.toml").exists()


def test_package_imports():
    """Test that basic package imports work (syntax check)"""
    # These will fail without dependencies installed, but syntax should be valid
    import ast
    
    project_root = Path(__file__).parent.parent
    
    # Check main.py syntax
    with open(project_root / "main.py") as f:
        ast.parse(f.read())
    
    # Check settings.py syntax
    with open(project_root / "src" / "config" / "settings.py") as f:
        ast.parse(f.read())
    
    # Check app.py syntax
    with open(project_root / "src" / "web" / "app.py") as f:
        ast.parse(f.read())
    
    # Check scheduler.py syntax
    with open(project_root / "src" / "services" / "scheduler.py") as f:
        ast.parse(f.read())
    
    # Check alert_manager.py syntax
    with open(project_root / "src" / "services" / "alert_manager.py") as f:
        ast.parse(f.read())