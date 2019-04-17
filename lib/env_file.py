#!/usr/bin/python

# Copyright: (c) 2019, Roman Hargrave <roman@hargrave.info>
# GNU General Public License 3.0 (https://www.gnu.org/licenses/gpl-3.0.txt)

ANSIBLE_METADATA = {
        'metadata_version': '1.1',
        'status': ['beta'],
        'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: env_file

short_description: "Read and write files containing POSIX shell assignments"

version_added: 2.7

description:
    - "Reads and writes POSIX shell files containing variable assignments, optionally prefixed with the export builtin."

options:
    path:
        description:
            - "Path to the file"
        required: true
    var:
        description:
            - "Environment variable name"
        required: true
    val:
        description:
            - "Value of the variable, applicable for state=present"
        required: false
    state:
        descritpion:
            - "State of the variable. absent, present, local, or exported. If present, the scope of the variable will be kept."
        required: true
    create:
        description:
            - "Whether the file should be created"
        required: false
    mode:
        description:
            - "Mode the file should be created with"
        required: false

author:
    - Roman Hargrave <roman@hargrave.info>
'''

EXAMPLES = '''
- name: Update environment variable
  env_file:
    path: /etc/profile.d/environment-variable.sh
    export: true
    var: ENVIRONMENT_VAR
    val: "environment variable"
    state: present
'''

import os
import re
import tempfile

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six import b
from ansible.module_utils._text import to_bytes, to_native

# matches a declaration with the given name
def match_var(name, line):
    name = re.escape(name)
    return re.match('^\s*#?\s*%s=.*$' % name, line) or re.match('^\s*#?\s*export\s+%s=.*$' % name, line)

# matches a declaration with the given name, as long as it isn't commented out
def match_active_var(name, line):
    name = re.escape(name)
    return re.match('^\s*%s=.*$' % name, line) or re.match('^\s*export\s+%s=.*$' % name, line)

def is_exported(line):
    return re.match('^\s*export\s*.+$', line)

# Process a value so that it may be safely inserted in to a shell script or prompt
def shell_escape(text):
    try:
        return __import__("shlex", fromlist=['quote']).quote(text)
    except:
        return "'%s'" % text.replace("'", "'\\''")

# Create or update a variable declaration
def apply(module, target, var, val, state, create, backup):
    diff = dict(
            before = '',
            after  = '',
            before_header = '%s (content)' % target,
            after_header  = '%s (content)' % target,
    )

    msg = ''
    env_lines = []

    if not os.path.exists(target):
        if not create:
            module.fail_json(rc=257, msg='Target file "%s" does not exist' % target)
        target_dir = os.path.dirname(target)
        if not os.path.exists(target_dir) and not module.check_mode:
            os.makedirs(target_dir)
    else:
        target_file = open(target, 'r')
        try:
            env_lines = target_file.readlines()
        finally:
            target_file.close()

    if module._diff:
        diff['before'] = ''.join(env_lines)

    changed = False

    # Guard against empty file
    if not env_lines:
        env_lines.append('\n')

    # Ensure a trailing newline is present
    if env_lines[-1] == "" or env_lines[-1][-1] != '\n':
        ini_lines[-1] += '\n'
        changed = True

    matched_line = False
    line_value = '%s=%s\n' % (var, shell_escape(val))

    # Match and update or delete existing variables
    for index, line in enumerate(env_lines):
        if state == 'absent':
            if match_active_var(var, line):
                del env_lines[index]
                changed = True
                msg = 'var removed'
                break
        else:
            # Extant var check
            if match_var(var, line):
                new_line = line_value

                # Determine if the line is already exported and whether it should remain that way
                should_export = (state == 'exported') or (state == 'present' and is_exported(line))

                if should_export:
                    new_line = 'export %s' % new_line

                var_changed = env_lines[index] != new_line
                changed = changed or var_changed

                env_lines[index] = new_line
                matched_line = True

                if var_changed:
                    msg = 'var changed'
                    index += 1
                    while index < len(env_lines):
                        line = env_lines[index]
                        if match_active_var(var, line):
                            del env_lines[index]
                        else:
                            index += 1
                break

    # Add the variable, if not extant
    # If state == present, this will operate in the same manner as state == exported
    if not state == 'absent' and not matched_line:
        new_line = line_value

        if not state == 'local':
            new_line = 'export %s' % new_line

        env_lines.append(new_line)
        msg = 'var added'
        changed = True

    backup_file = None
    if changed and not module.check_mode:
        if backup:
            backup_file = module.backup_local(target)

        try:
            tmpfd, tmpfile = tempfile.mkstemp(dir = module.tmpdir)
            f = os.fdopen(tmpfd, 'w')
            f.writelines(env_lines)
            f.close()
        except IOError:
            module.fail_json(msg='Unable to create temporary file %s' % tmpfile, traceback=traceback.format_exc())

        try:
            module.atomic_move(tmpfile, target)
        except IOError:
            module.ansible.fail_json(msg='Unable to move temporary file %s to %s, IOError' % (tmpfile, target), traceback=traceback.format_exc())

    return (changed, backup_file, diff, msg)

def main():
    module = AnsibleModule(
            argument_spec = dict(
                path     = dict(type = 'path', required = True),
                state    = dict(type = 'str',  default  = 'present', choices = ['absent', 'present', 'local', 'exported']),
                create   = dict(type = 'bool', default  = False),
                backup   = dict(type = 'bool', default  = False),
                var      = dict(type = 'str',  required = True, aliases=['name', 'variable']),
                val      = dict(type = 'str',  default = '', aliases=['content', 'value']),
            ),
            add_file_common_args = True,
            supports_check_mode  = True,
    )

    params = module.params
    create = params['create']
    backup = params['backup']
    path   = params['path']
    var    = params['var']
    val    = params['val']
    state  = params['state']

    bin_path = to_bytes(path, errors='surrogate_or_strict')

    if os.path.isdir(bin_path):
        module.fail_json(rc=256, msg='Path %s is a directory' % path)

    (changed, backup_file, diff, msg) = apply(module, path, var, val, state, create, backup)

    if not module.check_mode and os.path.exists(path):
        file_args = module.load_file_common_arguments(module.params)
        changed   = module.set_fs_attributes_if_different(file_args, changed)

    results = dict(
            changed = changed,
            diff    = diff,
            msg     = msg,
            path    = path
    )

    if backup_file is not None:
        results['backup_file'] = backup_file

    module.exit_json(**results)

if __name__ == '__main__':
    main()
