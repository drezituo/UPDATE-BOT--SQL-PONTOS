import unittest
from contextlib import contextmanager
from unittest.mock import Mock,patch
from importar_nomes_site import clean_name,preview,apply,csv_report

class NamesTests(unittest.TestCase):
 def db(self,cur):
  @contextmanager
  def connect(*args):yield cur
  return patch('importar_nomes_site.database',connect)
 def test_matching_number_prefix_only(self):
  self.assertEqual(clean_name('#013 André',13),'André')
  self.assertEqual(clean_name('#99 André',13),'#99 André')
  self.assertEqual(clean_name('#13 André',None),'#13 André')
  self.assertEqual(clean_name('𝕾𝖎𝖝𝖒𝖒𝖗𝖊𝖈',153),'𝕾𝖎𝖝𝖒𝖒𝖗𝖊𝖈')
 def test_invalid_names_are_not_truncated(self):
  for name in ('','a'*61):
   with self.assertRaises(ValueError):clean_name(name)
 def test_preview_is_read_only_and_maps_by_id(self):
  cur=Mock();cur.fetchall.side_effect=[[(1,'Nome antigo'),(2,'Mesmo')],[(1,13)]]
  with self.db(cur):rows=preview('url',[{'id':1,'display_name':'#13 Novo','username':'u1'},{'id':2,'display_name':'Mesmo','username':'u2'},{'id':3,'display_name':'Novo','username':'u3'}])
  self.assertEqual([r['status'] for r in rows],['atualizar','sem_alteracao','criar_perfil'])
  self.assertEqual(rows[0]['new'],'Novo');self.assertEqual(rows[2]['user_id'],3)
  self.assertTrue(all(c.args[0].startswith('SELECT') for c in cur.execute.call_args_list))
 def test_apply_changes_only_public_name_and_audits_old_value(self):
  cur=Mock();cur.fetchone.return_value=(1,)
  row={'user_id':1,'old':'Antigo','new':'Novo','existed':True,'status':'atualizar'}
  with self.db(cur):result=apply('url',[row],99,42)
  self.assertEqual(result[0]['status'],'atualizado')
  sql,params=cur.execute.call_args_list[1].args
  self.assertIn('nome_publico IS NOT DISTINCT FROM',sql);self.assertEqual(params,('Novo',1,'Antigo'))
  self.assertNotIn('primeiro_nome',sql);self.assertNotIn('ultimo_nome',sql)
  self.assertEqual(cur.execute.call_args.args[1],(1,99,'Antigo','Novo',42))
 def test_concurrent_profile_changes_are_preserved(self):
  for exists in (False,True):
   cur=Mock();cur.fetchone.return_value=None
   row={'user_id':1,'old':'A' if exists else None,'new':'B','existed':exists,'status':'atualizar' if exists else 'criar_perfil'}
   with self.db(cur):result=apply('url',[row],99,42)
   self.assertEqual(result[0]['status'],'alterado_entretanto_preservado');self.assertEqual(cur.execute.call_count,2)
 def test_create_does_not_grant_membership_or_touch_other_tables(self):
  cur=Mock();cur.fetchone.return_value=(1,)
  row={'user_id':1,'old':None,'new':'Nome','existed':False,'status':'criar_perfil'}
  with self.db(cur):apply('url',[row],99,42)
  queries='\n'.join(c.args[0] for c in cur.execute.call_args_list)
  self.assertIn('ON CONFLICT(user_id) DO NOTHING',queries)
  for table in ['jogadores_numeros','portal_candidaturas','economia_carteiras','portal_termos_jogadores']:
   self.assertNotIn(table,queries)
 def test_csv_escapes_formula_like_names_and_keeps_ids_exact(self):
  data=csv_report([{'user_id':123456789012345678,'old':'=1+1','new':'@Nome','status':'atualizar'}]).decode('utf-8-sig')
  self.assertIn('123456789012345678',data);self.assertIn("'=1+1",data);self.assertIn("'@Nome",data)
