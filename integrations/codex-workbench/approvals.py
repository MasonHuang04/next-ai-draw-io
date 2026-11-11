"""Translate the official server request shapes into scoped client responses."""
from copy import deepcopy

SUPPORTED_METHODS = frozenset({
    'item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
    'item/permissions/requestApproval', 'item/tool/requestUserInput',
    'mcpServer/elicitation/request',
})


class UnsupportedApproval(ValueError):
    pass


def make_response(request, accept, answers=None, content=None):
    method = request['method']
    params = request['params']
    if method in SUPPORTED_METHODS and method != 'item/tool/requestUserInput' and not isinstance(accept, bool):
        raise ValueError('请明确选择允许或拒绝。')
    if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
        decision = 'accept' if accept else 'decline'
        allowed = params.get('availableDecisions')
        if allowed and decision not in allowed:
            decision = 'cancel' if not accept and 'cancel' in allowed else decision
            if decision not in allowed:
                raise ValueError('当前请求不支持此决定。')
        return {'decision': decision}
    if method == 'item/permissions/requestApproval':
        requested = params.get('permissions') or {}
        # A turn-scoped grant can never add permissions that were not requested.
        granted = {key: deepcopy(value) for key,value in requested.items()
                   if key in ('network', 'fileSystem') and value is not None} if accept else {}
        return {'permissions': granted, 'scope': 'turn'}
    if method == 'item/tool/requestUserInput':
        expected = {question['id'] for question in params.get('questions', [])}
        supplied = answers or {}
        if set(supplied) != expected:
            raise ValueError('请回答当前请求中的全部问题。')
        for answer in supplied.values():
            values = answer.get('answers') if isinstance(answer, dict) else None
            if not isinstance(values, list) or not values or not all(isinstance(x,str) and x.strip() for x in values):
                raise ValueError('回答不能为空。')
        return {'answers': supplied}
    if method == 'mcpServer/elicitation/request':
        if not accept:
            return {'action':'decline', 'content':None}
        if params.get('mode') == 'url':
            # Completion is confirmed by the user; no credentials are read or injected.
            return {'action':'accept', 'content':None}
        schema = params.get('requestedSchema') or {}
        validate_form(content, schema)
        return {'action':'accept', 'content':content}
    raise UnsupportedApproval('此请求类型尚未接入：' + method)


def validate_form(content, schema):
    if not isinstance(content,dict):
        raise ValueError('请填写表单。')
    properties=schema.get('properties',{})
    if any(key not in properties for key in content):
        raise ValueError('表单包含未请求的字段。')
    for required in schema.get('required',[]):
        if required not in content:
            raise ValueError('缺少必填字段：'+required)
    for key,value in content.items():
        spec=properties[key]
        kind=spec.get('type')
        valid={'string':isinstance(value,str),'boolean':isinstance(value,bool),
               'integer':isinstance(value,int) and not isinstance(value,bool),
               'number':isinstance(value,(int,float)) and not isinstance(value,bool),
               'array':isinstance(value,list)}
        if kind in valid and not valid[kind]:
            raise ValueError('字段类型错误：'+key)
        if 'enum' in spec and value not in spec['enum']:
            raise ValueError('字段值不在可选范围：'+key)
        if isinstance(value,str):
            if len(value)<spec.get('minLength',0) or len(value)>spec.get('maxLength',100000):
                raise ValueError('字段长度不符合要求：'+key)
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            if value<spec.get('minimum',float('-inf')) or value>spec.get('maximum',float('inf')):
                raise ValueError('字段数值不符合要求：'+key)
