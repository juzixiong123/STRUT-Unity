import argparse
import re


def read_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


def write_content(output_path, modified_content):
    with open(output_path, 'w', encoding='utf-8') as f:
        for line in modified_content.splitlines():
            if line.strip():
                f.write(line + '\n')


def write_file(path, content):
    with open(path, 'w', encoding='gbk') as f:
        f.write(content)


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('fun_path', type=str, default=None)
    parser.add_argument('case_path', type=str, default=None)
    parser.add_argument('uncover_path', type=str, nargs='?', default=None)
    args = parser.parse_args()
    # sig_info_path = args.sig_path
    function_path = args.fun_path
    case_path = args.case_path
    uncover_path = ''
    if args.uncover_path:
        uncover_path = args.uncover_path
    return function_path, case_path, uncover_path


def get_file(path: str):
    with open(path, encoding='gbk') as f:
        it = iter(f)
        content = ''
        for line in it:
            content += line
        return content


def determine_type(input_str):
    if re.fullmatch(r'-?\d+', input_str):
        return "Integer"

    if re.fullmatch(r'-?0[xX][0-9a-fA-F]+|"(-?0[xX][0-9a-fA-F]+)"', input_str):
        return "Hexadecimal"

    if re.fullmatch(r'-?\d+\.\d+', input_str):
        return "Float"

    if re.fullmatch(r"'(\\?.)'", input_str):
        return "Character"

    if re.fullmatch(r'"[^"]*"', input_str):
        return "String"

    return "Unknown"
