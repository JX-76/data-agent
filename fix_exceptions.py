#!/usr/bin/env python3
"""Fix bare except Exception in Python files."""

import os
import re


def fix_file(filepath):
    """Fix bare except Exception in a file."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    
    # Pattern 1: except Exception: (no as e)
    # Replace with except Exception as e: + logger.warning
    content = re.sub(
        r'        except Exception:\s*\n            (\w+)\(',
        r'        except Exception as e:\n            logger.warning("error_handled", error=str(e))\n            \1(',
        content
    )
    
    # Pattern 2: except Exception as e: (already has e)
    # Just ensure it has logger
    content = re.sub(
        r'        except Exception as e:\s*\n            (\w+)\(',
        r'        except Exception as e:\n            logger.warning("error_handled", error=str(e))\n            \1(',
        content
    )
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed: {filepath}")
        return True
    return False


def main():
    src_dir = "src"
    fixed = 0
    
    for root, dirs, files in os.walk(src_dir):
        for filename in files:
            if filename.endswith('.py'):
                filepath = os.path.join(root, filename)
                if fix_file(filepath):
                    fixed += 1
    
    print(f"Fixed {fixed} files")


if __name__ == "__main__":
    main()
