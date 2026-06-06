import re

from util import determine_type


def make_case_list(data, has_stub: bool, default_PTR):
    """Constructing a list of test cases"""
    if has_stub:
        pattern = re.compile(
            r'{\s*"desc":\s*"(?P<desc>[^"]+)",'
            r'\s*"inputs":\s*\[(?P<inputs>.*?)\],'
            r'\s*"stubins":\s*\[(?P<stubins>.*?)\],'
            r'\s*"outputs":\s*\[(?P<outputs>.*?)\]\s*}',
            re.DOTALL
        )
    else:
        pattern = re.compile(
            r'{\s*"desc":\s*"(?P<desc>[^"]+)",'
            r'\s*"inputs":\s*\[(?P<inputs>.*?)\],'
            r'\s*"outputs":\s*\[(?P<outputs>.*?)\]\s*}',
            re.DOTALL
        )

    matches = pattern.finditer(data)
    test_cases = []

    for match in matches:
        desc = match.group('desc')
        inputs = match.group('inputs')
        outputs = match.group('outputs')
        stubins = []
        if has_stub:
            stubins = match.group('stubins')

        input_pattern = re.compile(
            r'{\s*"expr":\s*"(?P<expr>[^"]+)",\s*"value":\s*(?:'
            r'(?P<value_hex>"(0[xX][0-9a-fA-F]+)"|0[xX][0-9a-fA-F]+)|'  # matches a hexadecimal value
            r'(?P<value_float>-?\d+\.\d+)|'  # matches floating point values
            r'(?P<value_int>-?\d+)|'  # matches integer values
            r'(?P<value_str>"[^"]+")|'  # matches string values
            # r'(?P<value_char>\'\\?.\')\s*|'  # matches Characters
            r'\[\s*(?P<value_bracket>(?:-?\d+\.\d+|-?\d+|0[xX][0-9a-fA-F]+|"(0[xX][0-9a-fA-F]+)"|"[^"]+")\s*'
            r'(?:,\s*(?:-?\d+\.\d+|-?\d+|0[xX][0-9a-fA-F]+|"(0[xX][0-9a-fA-F]+)"|"[^"]+"))*)\s*\]'  # matches a value enclosed by square brackets
            r')\s*}'
        )

        output_pattern = re.compile(
            r'{\s*"expr":\s*"(?P<expr>[^"]+)",'
            r'\s*"value":\s*(?:"(?P<value_str>[^"]+)"|(?P<value_hex>0[xX][0-9a-fA-F]+)|(?P<value_float>-?\d+\.\d+)|(?P<value_int>-?\d+))\s*}'
        )
        stub_list = []
        if has_stub:
            stub_pattern = re.compile(
                r'{\s*"funcName":\s*"(?P<funcName>[^"]+)",\s*"expr":\s*"(?P<expr>[^"]+)",'
                r'\s*"value":\s*(?:(?P<value>\[\s*((?:(?:-?\d+\.\d+)|(?:-?\d+)|(?:"[^"]*")|(?:\'[^\']*\')|(?:0[xX][0-9a-fA-F]+))(\s*,\s*(?:(?:-?\d+\.\d+)|(?:-?\d+)|(?:"[^"]*")|(?:\'[^\']*\')|(?:0[xX][0-9a-fA-F]+)))*\s*)\]))\s*}'
            )
            stub_list = [m.groupdict() for m in stub_pattern.finditer(stubins)]

        input_list = [m.groupdict() for m in input_pattern.finditer(inputs)]
        output_list = [m.groupdict() for m in output_pattern.finditer(outputs)]

        bracket_list = []
        # process input value
        for item in input_list:
            if item['value_hex'] is not None:
                value_hex = item['value_hex']
                if value_hex.find('"') != -1:
                    value_hex = value_hex.replace('"', '')
                # temp = int(value_hex, 16)  # process hex number
                item['value'] = value_hex
            elif item['value_float'] is not None:
                item['value'] = float(item['value_float'])
            elif item['value_int'] is not None:
                item['value'] = int(item['value_int'])
            elif item['value_bracket'] is not None:
                '''array value'''
                expr = item['expr']
                bracket_value = item['value_bracket'].split(',')
                i = 0
                for value in bracket_value:
                    value = value.replace(' ', '')
                    # get array type
                    if determine_type(value) == 'Integer':
                        value = int(value)
                    elif determine_type(value) == 'Float':
                        value = float(value)
                    elif determine_type(value) == 'Hexadecimal':
                        if value.find('"') != -1:
                            value = value.replace('"', '')
                    inp = {'expr': expr + '[' + str(i) + ']', 'value': value}
                    bracket_list.append(inp)
                    i += 1
                item['value'] = 'delete'
            else:
                value_str = item['value_str']
                if value_str.find('"') != -1:
                    value_str = value_str.replace('"', '')
                item['value'] = value_str

            del item['value_str']
            del item['value_hex']
            del item['value_float']
            del item['value_int']
            del item['value_bracket']
        # delete data
        for i in range(len(input_list) - 1, -1, -1):
            if input_list[i]['value'] == 'delete':
                del input_list[i]
        for item in bracket_list:
            input_list.append(item)
        # convert type
        for item in output_list:
            if item['value_hex'] is not None:
                item['value'] = item['value_hex']  # 处理十六进制数
            elif item['value_float'] is not None:
                item['value'] = float(item['value_float'])
            elif item['value_int'] is not None:
                item['value'] = int(item['value_int'])
            else:
                item['value'] = item['value_str']
            del item['value_str']
            del item['value_hex']
            del item['value_float']
            del item['value_int']

        for item in stub_list:
            value: str = item['value']
            value = value.replace('[', '').replace(']', '').replace(',', ';')
            item['value'] = value
            # process function pointer
            for ite in default_PTR:
                if ite['expr'] == item['funcName']:
                    item['funcName'] = ite['userVar']

        test_cases.append({
            "desc": desc,
            "inputs": input_list,
            "stubins": stub_list,
            "outputs": output_list,
            "doBoundary": 0,
            "ioins": [],
        })
    return test_cases
