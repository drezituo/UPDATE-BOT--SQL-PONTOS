import io
import unittest
from contextlib import contextmanager
from datetime import datetime,timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from PIL import Image
from importar_termos_site import action_for,plan,select_attachment,validate_bytes,save_document

class TermsImportTests(unittest.TestCase):
 def setUp(self):
  self.row=dict(id=7,user_id=123,file_name='termo.pdf',estado='aprovado',revisto_por=456,revisto_em=datetime.now(timezone.utc),staff_channel_id=99,staff_message_id=88,confirmado=True,site_estado=None,site_documento=False,site_origem=None)
 def test_existing_documents_and_site_decisions_are_preserved(self):
  for state in ['pendente','rejeitado','aprovado']:
   self.assertEqual(action_for(dict(estado=state,tem_documento=True,origem_validacao='site')),'preservar')
  self.assertEqual(action_for(dict(estado='aprovado',tem_documento=False,origem_validacao='site')),'preservar')
  self.assertEqual(action_for(dict(estado='aprovado',tem_documento=False,origem_validacao='discord_manual')),'anexar')
 def test_only_confirmed_approved_uploads_qualify(self):
  self.assertEqual(plan(self.row),'criar')
  for key,value in [('estado','pendente'),('estado','rejeitado'),('confirmado',False),('revisto_por',None),('revisto_em',None)]:
   self.assertEqual(plan({**self.row,key:value}),'sem_aprovacao_confirmada')
  self.assertEqual(plan({**self.row,'staff_message_id':None}),'sem_mensagem_guardada')
 def message(self):
  return NS(author=NS(id=999),channel=NS(id=99),content='Termo de <@123>',embeds=[NS(footer=NS(text='Upload ID: 7'),fields=[])],attachments=[NS(filename='termo.pdf',size=100)])
 def test_exact_message_and_attachment_matching(self):
  m=self.message();self.assertEqual(select_attachment(self.row,m,999).filename,'termo.pdf')
  for change in ['author','user','footer','filename','duplicate','size']:
   m=self.message()
   if change=='author':m.author.id=100
   if change=='user':m.content='Termo de <@1234>'
   if change=='footer':m.embeds[0].footer.text='Upload ID: 8'
   if change=='filename':m.attachments[0].filename='outro.pdf'
   if change=='duplicate':m.attachments*=2
   if change=='size':m.attachments[0].size=21*1024*1024
   with self.subTest(change=change),self.assertRaises(ValueError):select_attachment(self.row,m,999)
 def test_bytes_are_validated_without_reencoding(self):
  for fmt,mime in [('JPEG','image/jpeg'),('PNG','image/png')]:
   out=io.BytesIO();Image.new('RGB',(12,8),'red').save(out,format=fmt);data=out.getvalue();before=data
   self.assertEqual(validate_bytes(data),mime);self.assertEqual(data,before)
  self.assertEqual(validate_bytes(b'%PDF-1.4\nexample'),'application/pdf')
  for data in [b'',b'<html>',b'\x89PNG\r\n\x1a\n']:
   with self.assertRaises(ValueError):validate_bytes(data)
 def save(self,answers):
  cur=Mock();cur.fetchone.side_effect=answers
  @contextmanager
  def db(*args):yield cur
  with patch('importar_termos_site.database',db):result=save_document('unused',self.row,b'%PDF-1.4', 'application/pdf',999)
  return result,cur
 def test_create_copies_approval_and_records_audit(self):
  result,cur=self.save([('aprovado',456,self.row['revisto_em']),('123',)])
  self.assertEqual(result,'criado')
  args=cur.execute.call_args_list[1].args[1];self.assertEqual(args,('123',b'%PDF-1.4','application/pdf','456',self.row['revisto_em']))
  self.assertIn('portal_termos_importacoes_discord',cur.execute.call_args.args[0])
 def test_concurrent_site_upload_is_not_overwritten(self):
  result,cur=self.save([('aprovado',456,self.row['revisto_em']),None,None])
  self.assertEqual(result,'preservado');self.assertEqual(cur.execute.call_count,3)
  update=cur.execute.call_args.args[0];self.assertIn("origem_validacao='discord_manual'",update);self.assertIn('documento_dados IS NULL',update)
 def test_append_keeps_existing_manual_review_metadata(self):
  result,cur=self.save([('aprovado',456,self.row['revisto_em']),None,('123',)])
  self.assertEqual(result,'anexado');update=cur.execute.call_args_list[2].args[0]
  self.assertNotIn('validado_por=',update);self.assertNotIn('validado_em=',update)
 def test_source_rejection_during_download_prevents_import(self):
  result,cur=self.save([('rejeitado',456,self.row['revisto_em'])]);self.assertEqual(result,'origem_alterada');self.assertEqual(cur.execute.call_count,1)
