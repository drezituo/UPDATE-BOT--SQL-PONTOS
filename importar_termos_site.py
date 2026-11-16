"""Import approved Discord terms into the existing portal, preserving original files."""
import asyncio
import csv
import hashlib
import io
import re
from contextlib import contextmanager
from collections import Counter
from PIL import Image

MAX_BYTES=20*1024*1024
SCHEMA='''CREATE TABLE IF NOT EXISTS public.portal_termos_importacoes_discord (
 upload_id BIGINT PRIMARY KEY,
 user_id TEXT NOT NULL,
 canal_id BIGINT NOT NULL,
 mensagem_id BIGINT NOT NULL,
 nome_ficheiro TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 tamanho_bytes INTEGER NOT NULL,
 acao TEXT NOT NULL,
 importado_por TEXT NOT NULL,
 importado_em TIMESTAMPTZ NOT NULL DEFAULT now()
)'''

@contextmanager
def database(url,read_only=False):
    import psycopg2
    conn=psycopg2.connect(url,connect_timeout=5,options='-c statement_timeout=15000')
    try:
        with conn:
            with conn.cursor() as cur:
                if read_only:cur.execute('SET TRANSACTION READ ONLY')
                yield cur
    finally:conn.close()

def action_for(target):
    if target is None:return 'criar'
    if target['estado']=='aprovado' and not target['tem_documento'] and target['origem_validacao']=='discord_manual':return 'anexar'
    return 'preservar'

def inspect_data(url,channel_id):
    with database(url,True) as cur:
        cur.execute('''SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='portal_termos_jogadores' ''')
        required={'user_id','documento_dados','documento_mime','estado','validado_por','validado_em','origem_validacao'}
        if not required<={r[0] for r in cur.fetchall()}:raise ValueError('O esquema de termos do site não está disponível nesta base de dados (migrações 009 e 055).')
        cur.execute('''WITH latest AS (
          SELECT DISTINCT ON (user_id) id,user_id,file_name,estado,revisto_por,revisto_em,staff_channel_id,staff_message_id
          FROM public.termos_uploads ORDER BY user_id,enviado_em DESC,id DESC
        ) SELECT u.id,u.user_id,u.file_name,u.estado,u.revisto_por,u.revisto_em,u.staff_channel_id,u.staff_message_id,
          (r.user_id IS NOT NULL) AS confirmado,
          p.estado,p.documento_dados IS NOT NULL,p.origem_validacao
          FROM latest u LEFT JOIN public.termos_responsabilidade r ON r.user_id=u.user_id
          LEFT JOIN public.portal_termos_jogadores p ON p.user_id=u.user_id::text
          WHERE u.staff_channel_id=%s ORDER BY u.id''',(channel_id,))
        names=['id','user_id','file_name','estado','revisto_por','revisto_em','staff_channel_id','staff_message_id','confirmado','site_estado','site_documento','site_origem']
        rows=[dict(zip(names,r)) for r in cur.fetchall()]
        cur.execute('''SELECT r.user_id FROM public.termos_responsabilidade r
          WHERE NOT EXISTS(SELECT 1 FROM public.termos_uploads u WHERE u.user_id=r.user_id)''')
        no_upload=[str(r[0]) for r in cur.fetchall()]
    return rows,no_upload

def plan(row):
    if row['estado']!='aprovado' or not row['confirmado'] or not row['revisto_por'] or not row['revisto_em']:return 'sem_aprovacao_confirmada'
    if not row['staff_message_id']:return 'sem_mensagem_guardada'
    target=None if row['site_estado'] is None else {'estado':row['site_estado'],'tem_documento':row['site_documento'],'origem_validacao':row['site_origem']}
    return action_for(target)

def select_attachment(row,message,bot_id):
    if message.author.id!=bot_id or message.channel.id!=row['staff_channel_id']:
        raise ValueError('A mensagem não pertence ao bot/canal esperado')
    expected='Upload ID: '+str(row['id'])
    if not any(getattr(e.footer,'text',None)==expected for e in message.embeds):
        raise ValueError('O identificador do upload não coincide com a mensagem')
    identity=message.content+' '+ ' '.join(str(f.value) for e in message.embeds for f in e.fields)
    if not re.search(r'<@!?'+str(row['user_id'])+r'>',identity):raise ValueError('O jogador da mensagem não coincide com o registo')
    files=[a for a in message.attachments if a.filename==row['file_name']]
    if len(files)!=1:raise ValueError('Anexo ausente ou ambíguo')
    if not 0<files[0].size<=MAX_BYTES:raise ValueError('Anexo vazio ou superior a 20 MB')
    return files[0]

def validate_bytes(data):
    if not data or len(data)>MAX_BYTES:raise ValueError('Ficheiro vazio ou superior a 20 MB')
    if data.startswith(b'%PDF-'):return 'application/pdf'
    if data.startswith(b'\xff\xd8\xff'):mime,fmt='image/jpeg','JPEG'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):mime,fmt='image/png','PNG'
    else:raise ValueError('Formato de ficheiro não suportado')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format!=fmt:raise ValueError('Formato inconsistente')
            image.verify()
    except (OSError,SyntaxError,Image.DecompressionBombError) as exc:raise ValueError('Imagem inválida ou demasiado grande') from exc
    return mime

def prepare_audit(url):
    with database(url) as cur:cur.execute(SCHEMA)

def save_document(url,row,data,mime,actor):
    """Source recheck and conditional destination writes occur in the same transaction."""
    with database(url) as cur:
        cur.execute('''SELECT u.estado,u.revisto_por,u.revisto_em FROM public.termos_uploads u
          JOIN public.termos_responsabilidade r ON r.user_id=u.user_id
          WHERE u.id=%s AND u.user_id=%s AND u.staff_channel_id=%s AND u.staff_message_id=%s
          AND u.file_name=%s AND u.id=(SELECT x.id FROM public.termos_uploads x WHERE x.user_id=u.user_id ORDER BY x.enviado_em DESC,x.id DESC LIMIT 1)
          FOR SHARE OF u,r''',(row['id'],row['user_id'],row['staff_channel_id'],row['staff_message_id'],row['file_name']))
        source=cur.fetchone()
        if not source or source[0]!='aprovado' or not source[1] or not source[2]:return 'origem_alterada'
        cur.execute('''INSERT INTO public.portal_termos_jogadores
          (user_id,documento_dados,documento_mime,estado,validado_por,validado_em,origem_validacao,atualizado_em)
          VALUES (%s,%s,%s,'aprovado',%s,%s,'discord_import',now())
          ON CONFLICT(user_id) DO NOTHING RETURNING user_id''',
          (str(row['user_id']),data,mime,str(source[1]),source[2]))
        action='criado'
        if not cur.fetchone():
            # Only attach to an explicitly Discord-manual approval; never change its review metadata.
            cur.execute("""UPDATE public.portal_termos_jogadores SET documento_dados=%s,documento_mime=%s,atualizado_em=now()
              WHERE user_id=%s AND estado='aprovado' AND origem_validacao='discord_manual'
              AND documento_dados IS NULL RETURNING user_id""",(data,mime,str(row['user_id'])))
            if not cur.fetchone():return 'preservado'
            action='anexado'
        cur.execute('''INSERT INTO public.portal_termos_importacoes_discord
          (upload_id,user_id,canal_id,mensagem_id,nome_ficheiro,sha256,tamanho_bytes,acao,importado_por)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(upload_id) DO NOTHING''',
          (row['id'],str(row['user_id']),row['staff_channel_id'],row['staff_message_id'],row['file_name'],hashlib.sha256(data).hexdigest(),len(data),action,str(actor)))
        return action

def register(bot,database_url,channel_id):
    import discord
    from discord.ext import commands
    running=asyncio.Lock()

    @bot.command(name='termos_site')
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def import_terms(ctx,modo:str='verificar'):
        mode=modo.lower().strip()
        if ctx.channel.id!=channel_id:
            return await ctx.reply('Executa este comando no canal privado dos termos.',mention_author=False)
        if mode not in ('verificar','importar'):
            return await ctx.reply('Usa `!termos_site verificar` ou `!termos_site importar`.',mention_author=False)
        if running.locked():return await ctx.reply('Já existe uma verificação/importação em curso.',mention_author=False)
        async with running:
            status=await ctx.reply('A verificar aprovações e anexos. Nenhum termo será enviado aos jogadores.',mention_author=False)
            try:
                rows,no_upload=await asyncio.to_thread(inspect_data,database_url,channel_id)
                if mode=='importar':await asyncio.to_thread(prepare_audit,database_url)
                results=[];counts=Counter()
                for index,row in enumerate(rows,1):
                    action=plan(row);detail=''
                    if action in ('criar','anexar'):
                        try:
                            message=await ctx.channel.fetch_message(row['staff_message_id'])
                            attachment=select_attachment(row,message,bot.user.id)
                            file=await attachment.to_file()
                            try:
                                file.fp.seek(0);data=file.fp.read(MAX_BYTES+1)
                            finally:file.close()
                            mime=await asyncio.to_thread(validate_bytes,data)
                            action=('pronto_'+action) if mode=='verificar' else await asyncio.to_thread(save_document,database_url,row,data,mime,ctx.author.id)
                        except discord.NotFound:action,detail='ficheiro_indisponivel','Mensagem ou anexo eliminado/inacessível'
                        except discord.Forbidden:action,detail='sem_permissao','Bot sem acesso à mensagem ou ao ficheiro'
                        except ValueError as exc:action,detail='verificar_manualmente',str(exc)
                        except Exception as exc:action,detail='erro',type(exc).__name__
                    counts[action]+=1
                    results.append([row['id'],str(row['user_id']),row['staff_message_id'],action,detail])
                    if index%10==0:await status.edit(content=f'Termos: {index}/{len(rows)} registos verificados. Modo: {mode}.')
                results.extend(['',uid,'','aprovacao_sem_upload','Sem registo de anexo no bot; verificar manualmente'] for uid in no_upload)
                report=io.StringIO();writer=csv.writer(report);writer.writerow(['upload_id','discord_user_id','mensagem_id','resultado','observacao']);writer.writerows(results)
                summary='\n'.join(f'• {key}: {count}' for key,count in sorted(counts.items())) or 'Sem uploads neste canal.'
                await status.edit(content=('Verificação concluída — nenhuma alteração na base de dados.' if mode=='verificar' else 'Importação concluída.')+'\n'+summary+f'\nAprovações antigas sem registo de upload: {len(no_upload)}.\nDocumentos indisponíveis não são marcados como importados.')
                await ctx.send(file=discord.File(io.BytesIO(report.getvalue().encode('utf-8-sig')),filename='relatorio_termos_site.csv'),allowed_mentions=discord.AllowedMentions.none())
            except Exception as exc:
                await status.edit(content='Não foi possível concluir. Tipo de erro: '+type(exc).__name__+'. Verifica a ligação PostgreSQL, as tabelas de termos do site e o acesso aos anexos. Podes repetir o comando; os termos existentes são preservados.')
