"""Scan exactly the staged tree before committing."""
import subprocess
from guard_push import check_tree
check_tree(subprocess.check_output(['git','write-tree']).decode().strip())
