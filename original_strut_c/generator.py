import json


def case_generator(case_content):
    case_content = json.loads(case_content)
    cases = case_content['cases']
    case_0 = cases[0]
    case = {'desc': 'description text'}
    case_input = case_0['inputs']
    # types = case_0['types']
    case_output = case_0['outputs']
    case_stubins: list = case_0['stubins']
    case['inputs'] = case_input
    has_stub = False
    if case_stubins:
        case['stubins'] = case_stubins
        has_stub = True
    case['outputs'] = case_output
    default_PTR = case_content['defaultPTR']
    return json.dumps(case), default_PTR, has_stub


def prompt_generator(fun, case, has_stub: bool):
    """"""
    if has_stub:
        text = (
            'Please generate some test cases for the following C function.I will provide you a test case example with json format. This example is divide into three parts:inputs,outputs,stubins.'
            'We use \'expr\', \'type\' and \'value\' key to represent an input or output data. In the stubins list, I\'ll provide the called function signature as well as the called function return value or pointer type parameters that the called function may change, and you\'ll need to decide which data to assign values to based on the test case.'
            'Please continue to generate with this format. Satisfy the following requirements.'
            '1.Assign values to the data according to the situation you want to test.'
            '2.Try to cover all branches.'
            '3.Use the stub function to simulate the return value of the called function.I will provide you with all the data that the called function may change.It is up to you to decide which values to change in each test.'
            '4.Output legal json format for each test case.'
        )
    else:
        text = (
            'Please generate some test cases for the following C function.I will provide you a test case example with json format. This example is divide into two parts:input and output.'
            'We use \'expr\', \'type\' and \'value\' key to represent an input or output data.'
            'Please continue to generate with this format. Satisfy the following requirements.'
            '1.Assign values to the data according to the situation you want to test.'
            '2.Try to cover all branches.'
            '3.Output legal json format for each test case.')
    prompts = text + """
****The function under test starts****
""" + fun + """
****The function under test ends****
""" + case + """
{other test cases}
"""
    return prompts


def opti_generator():
    """get the optimization prompts"""
    return """\nUser:Well done. But the following branches are not covered. I will provide you with the uncovered conditions. Please generate test cases for the uncovered branches. The test cases you design need to be able to reach this branch and cover this branch. Do not output test cases that are repeated with the previous ones.\n"""


def aout_file_generator(case_list, func_name, defaultPTR):
    """generate the file aunit need"""
    case = {'func': func_name, 'file': '', 'cases': []}
    for c in case_list:
        inputs = c['inputs']
        for inp in inputs:
            expr = inp['expr']
            index = expr.find('->')
            if index != -1:
                expr = expr.replace('->', '.')
                pointer_param = expr[:index]
                name = ''
                square = '[0]'
                if pointer_param.find('[' and ']') != -1:
                    start = pointer_param.find('[')
                    end = pointer_param.find(']')
                    name = pointer_param[:start]
                    square = pointer_param[start:end + 1]
                else:
                    name = pointer_param
                if any(item['expr'] == name for item in defaultPTR):
                    name += '_PTRTO' + square
                    inp['expr'] = '(' + name + ')' + expr[index:]
            else:
                end = expr.find('[')
                name = expr[:end]
                sufix = expr[end:]
                if any(item['expr'] == name for item in defaultPTR):
                    name += '_PTRTO' + sufix
                    inp['expr'] = name

        c['inputs'] = inputs
        case['cases'].append(c)
    case['userVar'] = []
    case['defaultPTR'] = defaultPTR
    case['ios'] = []
    case = json.dumps(case)
    return case
