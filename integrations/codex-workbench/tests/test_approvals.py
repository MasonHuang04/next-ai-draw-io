import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from approvals import make_response, UnsupportedApproval


class ApprovalProtocolTests(unittest.TestCase):
    def test_command_decision_respects_available_choices(self):
        request={'method':'item/commandExecution/requestApproval','params':{'availableDecisions':['accept','cancel']}}
        self.assertEqual(make_response(request,True),{'decision':'accept'})
        self.assertEqual(make_response(request,False),{'decision':'cancel'})

    def test_file_changes_are_single_decisions(self):
        request={'method':'item/fileChange/requestApproval','params':{}}
        self.assertEqual(make_response(request,True),{'decision':'accept'})
        self.assertEqual(make_response(request,False),{'decision':'decline'})

    def test_permission_grant_is_limited_to_requested_turn(self):
        permissions={'network':{'enabled':True},'fileSystem':{'write':['C:/project/diagram.drawio']},'unexpected':'never grant'}
        request={'method':'item/permissions/requestApproval','params':{'permissions':permissions}}
        result=make_response(request,True)
        self.assertEqual(result,{'permissions':{'network':{'enabled':True},'fileSystem':{'write':['C:/project/diagram.drawio']}},'scope':'turn'})
        result['permissions']['fileSystem']['write'].append('different')
        self.assertEqual(permissions['fileSystem']['write'],['C:/project/diagram.drawio'])
        self.assertEqual(make_response(request,False),{'permissions':{},'scope':'turn'})

    def test_user_input_is_scoped_to_question_ids(self):
        request={'method':'item/tool/requestUserInput','params':{'questions':[{'id':'q'}]}}
        self.assertEqual(make_response(request,True,{'q':{'answers':['yes']}}),{'answers':{'q':{'answers':['yes']}}})
        for value in ({},{'wrong':{'answers':['yes']}},{'q':{'answers':['']}}):
            with self.assertRaises(ValueError):make_response(request,True,value)

    def test_mcp_form_rejects_unrequested_or_invalid_fields(self):
        request={'method':'mcpServer/elicitation/request','params':{'mode':'form','requestedSchema':{'type':'object','properties':{'count':{'type':'integer','minimum':1,'maximum':3}},'required':['count']}}}
        self.assertEqual(make_response(request,True,content={'count':2}),{'action':'accept','content':{'count':2}})
        for value in ({},{'count':True},{'count':4},{'count':1,'unknown':'x'}):
            with self.assertRaises(ValueError):make_response(request,True,content=value)

    def test_unknown_request_is_explicit(self):
        with self.assertRaises(UnsupportedApproval):make_response({'method':'unknown','params':{}},True)


if __name__=='__main__':unittest.main()
