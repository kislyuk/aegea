import errno
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from shutil import get_terminal_size

import botocore

from .exceptions import AegeaException, GetFieldError


def CYAN(message=None):
    if message is None:
        return "\033[36m" if sys.stdout.isatty() else ""
    else:
        return CYAN() + message + ENDC()

def BLUE(message=None):
    if message is None:
        return "\033[34m" if sys.stdout.isatty() else ""
    else:
        return BLUE() + message + ENDC()

def YELLOW(message=None):
    if message is None:
        return "\033[33m" if sys.stdout.isatty() else ""
    else:
        return YELLOW() + message + ENDC()

def GREEN(message=None):
    if message is None:
        return "\033[32m" if sys.stdout.isatty() else ""
    else:
        return GREEN() + message + ENDC()

def RED(message=None):
    if message is None:
        return "\033[31m" if sys.stdout.isatty() else ""
    else:
        return RED() + message + ENDC()

def WHITE(message=None):
    if message is None:
        return "\033[37m" if sys.stdout.isatty() else ""
    else:
        return WHITE() + message + ENDC()

def UNDERLINE(message=None):
    if message is None:
        return "\033[4m" if sys.stdout.isatty() else ""
    else:
        return UNDERLINE() + message + ENDC()

def BOLD(message=None):
    if message is None:
        return "\033[1m" if sys.stdout.isatty() else ""
    else:
        return BOLD() + message + ENDC()

def ENDC():
    return "\033[0m" if sys.stdout.isatty() else ""

def border(i):
    return WHITE() + i + ENDC()

ansi_pattern = re.compile(r"(\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]")

def strip_ansi_codes(i):
    return re.sub(ansi_pattern, "", i)

def ansi_truncate(s, max_len):
    ansi_total_len = 0
    for ansi_code in ansi_pattern.finditer(s):
        ansi_code_start, ansi_code_end = ansi_code.span()
        if ansi_code_start > max_len + ansi_total_len - 1:
            break
        ansi_total_len += ansi_code_end - ansi_code_start
    if len(s) > max_len + ansi_total_len:
        return s[:max_len + ansi_total_len - 1] + "…"
    return s

def format_table(table, column_names=None, column_specs=None, max_col_width=32, auto_col_width=False):
    """
    Table pretty printer. Expects tables to be given as arrays of arrays::

        print(format_table([[1, "2"], [3, "456"]], column_names=['A', 'B']))
    """
    orig_col_args = dict(column_names=column_names, column_specs=column_specs)
    if len(table) > 0:
        col_widths = [0] * len(table[0])
    elif column_specs is not None:
        col_widths = [0] * (len(column_specs) + 1)
    elif column_names is not None:
        col_widths = [0] * len(column_names)
    my_col_names, id_column = [], None
    if column_specs is not None:
        column_names = ["Row"]
        column_names.extend([col["name"] for col in column_specs])
        column_specs = [{"name": "Row", "type": "float"}] + column_specs
    if column_names is not None:
        for i in range(len(column_names)):
            if column_names[i].lower() == "id":
                id_column = i
            my_col = ansi_truncate(str(column_names[i]), max_col_width if i not in {0, id_column} else 99)
            my_col_names.append(my_col)
            col_widths[i] = max(col_widths[i], len(strip_ansi_codes(my_col)))
    trunc_table = []
    for row in table:
        my_row = []
        for i in range(len(row)):
            my_item = ansi_truncate(str(row[i]), max_col_width if i not in {0, id_column} else 99)
            my_row.append(my_item)
            col_widths[i] = max(col_widths[i], len(strip_ansi_codes(my_item)))
        trunc_table.append(my_row)

    type_colormap = {"boolean": BLUE(),
                     "integer": YELLOW(),
                     "float": WHITE(),
                     "string": GREEN()}
    for t in "uint8", "int16", "uint16", "int32", "uint32", "int64":
        type_colormap[t] = type_colormap["integer"]
    type_colormap["double"] = type_colormap["float"]

    def col_head(i):
        if column_specs is not None:
            return BOLD() + type_colormap[column_specs[i]["type"]] + column_names[i] + ENDC()
        else:
            return BOLD() + WHITE() + column_names[i] + ENDC()

    formatted_table = [border("┌") + border("┬").join(border("─") * i for i in col_widths) + border("┐")]
    if len(my_col_names) > 0:
        padded_column_names = [col_head(i) + " " * (col_widths[i] - len(my_col_names[i]))
                               for i in range(len(my_col_names))]
        formatted_table.append(border("│") + border("│").join(padded_column_names) + border("│"))
        formatted_table.append(border("├") + border("┼").join(border("─") * i for i in col_widths) + border("┤"))

    for row in trunc_table:
        padded_row = [row[i] + " " * (col_widths[i] - len(strip_ansi_codes(row[i]))) for i in range(len(row))]
        formatted_table.append(border("│") + border("│").join(padded_row) + border("│"))
    formatted_table.append(border("└") + border("┴").join(border("─") * i for i in col_widths) + border("┘"))

    if auto_col_width:
        if not sys.stdout.isatty():
            raise AegeaException("Cannot auto-format table, output is not a terminal")
        table_width = len(strip_ansi_codes(formatted_table[0]))
        tty_cols, tty_rows = get_terminal_size()
        if table_width > max(tty_cols, 80):
            return format_table(table, max_col_width=max_col_width - 1, auto_col_width=True, **orig_col_args)
    return "\n".join(formatted_table)

def page_output(content, pager=None, file=None):
    if file is None:
        file = sys.stdout
    if not content.endswith("\n"):
        content += "\n"

    pager_process = None
    try:
        if file != sys.stdout or not file.isatty() or not content.startswith(border("┌")):
            raise AegeaException()
        content_lines = content.splitlines()
        content_rows = len(content_lines)

        tty_cols, tty_rows = get_terminal_size()

        naive_content_cols = max(len(i) for i in content_lines)
        if tty_rows > content_rows and tty_cols > naive_content_cols:
            raise AegeaException()

        content_cols = max(len(strip_ansi_codes(i)) for i in content_lines)
        if tty_rows > content_rows and tty_cols > content_cols:
            raise AegeaException()

        pager_process = subprocess.Popen(pager or os.environ.get("PAGER", "less -RS"), shell=True,
                                         stdin=subprocess.PIPE, stdout=file)
        pager_process.stdin.write(content.encode("utf-8"))
        pager_process.stdin.close()
        pager_process.wait()
        if pager_process.returncode != os.EX_OK:
            raise AegeaException()
    except Exception as e:
        if not (isinstance(e, IOError) and e.errno == errno.EPIPE):
            file.write(content)
    finally:
        try:
            pager_process.terminate()
        except BaseException:
            pass

def get_field(item, field):
    for element in field.split("."):
        try:
            item = getattr(item, element)
        except AttributeError:
            try:
                item = item.get(element)
            except AttributeError:
                raise GetFieldError('Unable to access field or attribute "{}" of {}'.format(field, item))
    return item

def format_datetime(d):
    from babel import dates
    from dateutil.tz import tzutc
    d = d.replace(microsecond=0)
    # Switch from UTC to local TZ
    d = d.astimezone(tz=None)
    return dates.format_timedelta(d - datetime.now(tzutc()), add_direction=True)

def format_cell(cell):
    if isinstance(cell, datetime):
        cell = format_datetime(cell)
    if isinstance(cell, timedelta):
        from babel import dates
        cell = dates.format_timedelta(-cell, add_direction=True)
    if isinstance(cell, (list, dict)):
        cell = json.dumps(cell, default=lambda x: str(x))
    return cell

def get_cell(resource, field, transform=None):
    cell = get_field(resource, field)
    if transform:
        try:
            cell = transform(cell, resource)
        except TypeError:
            cell = transform(cell)
    try:
        return ", ".join(i.name for i in cell.all()) if hasattr(cell, "all") else cell
    except botocore.exceptions.ClientError as e:
        if getattr(e, "response", None) and e.response.get("Error", {}).get("Code", {}) == "AccessDenied":
            return "[Access denied]"
        raise

def format_tags(cell, row):
    tags = {tag["Key"]: tag["Value"] for tag in cell} if cell else {}
    return ", ".join("{}={}".format(k, v) for k, v in tags.items())

def trim_names(names, *prefixes):
    for name in names:
        for prefix in prefixes:
            if name.startswith(prefix):
                name = name[len(prefix):]
        yield name

def format_number(n, fractional_digits=2):
    B = n
    KB = float(1024)
    MB = float(KB * 1024)
    GB = float(MB * 1024)
    TB = float(GB * 1024)

    if B < KB:
        return '{0}'.format(B)
    elif KB <= B < MB:
        return '{0:.{precision}f}K'.format(B / KB, precision=fractional_digits)
    elif MB <= B < GB:
        return '{0:.{precision}f}M'.format(B / MB, precision=fractional_digits)
    elif GB <= B < TB:
        return '{0:.{precision}f}G'.format(B / GB, precision=fractional_digits)
    elif TB <= B:
        return '{0:.{precision}f}T'.format(B / TB, precision=fractional_digits)

def tabulate(collection, args, cell_transforms=None):
    if cell_transforms is None:
        cell_transforms = {}
    tag_col = "Tags" if "Tags" in args.columns else "tags"
    cell_transforms.setdefault(tag_col, format_tags)
    if getattr(args, "json", None):
        table = [{f: get_cell(i, f, cell_transforms.get(f)) for f in args.columns} for i in collection]
        return json.dumps(table, indent=2, default=lambda x: str(x))
    else:
        table = [[get_cell(i, f, cell_transforms.get(f)) for f in args.columns] for i in collection]  # type: ignore
        if getattr(args, "sort_by", None):
            reverse = False
            if callable(args.sort_by):
                table = sorted(table, key=args.sort_by)
            else:
                if args.sort_by.endswith(":reverse"):
                    reverse = True
                    args.sort_by = args.sort_by[:-len(":reverse")]
                table = sorted(table, key=lambda x: x[args.columns.index(args.sort_by)], reverse=reverse)
        table = [[format_cell(c) for c in row] for row in table]  # type: ignore
        args.columns = list(trim_names(args.columns, *getattr(args, "trim_col_names", [])))
        format_args = dict(auto_col_width=True) if args.max_col_width == 0 else dict(max_col_width=args.max_col_width)
        return format_table(table, column_names=getattr(args, "display_column_names", args.columns), **format_args)
