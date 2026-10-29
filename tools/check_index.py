"""Scan exactly the staged tree before committing."""
import subprocess
from guard_push import check_tree
check_tree(subprocess.check_output(['git','-c','safe.directory=' + str(__import__('pathlib').Path(__file__).resolve().parents[1]),'-C',str(__import__('pathlib').Path(__file__).resolve().parents[1]),'write-tree']).decode().strip())
