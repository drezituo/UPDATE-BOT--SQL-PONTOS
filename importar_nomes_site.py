"""One-off guild display-name import keyed by Discord ID; no admission changes."""
import asyncio
import csv
import io
import re
from collections import Counter
from importar_termos_site import database

SCHEMA='''CREATE TABLE IF NOT EXISTS public.portal_nomes_importacoes_discord (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 user_id BIGINT NOT NULL,
 servidor_id BIGINT NOT NULL,
 nome_anterior TEXT,
 nome_novo TEXT NOT NULL,
 importado_por BIGINT NOT NULL,
 importado_em TIMESTAMPTZ NOT NULL DEFAULT now()
)'''

def clean_name(display,number=None,fallback=''):
    name=(display or fallback).strip()
    # Remove only the prefix which matches the player's existing STUNHOUSE number.
    if number is not None:
        match=re.match(r'^#(\d+)\s+',name)
        if match and int(match.group(1))==int(number):name=name[match.end():].strip() or fallback.strip()
    if not name or len(name)>60:raise ValueError('Nome vazio ou superior a 60 caracteres')
    return name

def preview(url,members):
    ids=[m['id'] for m in members]
    with database(url,True) as cur:
        cur.execute('SELECT user_id,nome_publico FROM public.portal_perfis WHERE user_id=ANY(%s)',(ids,))
        old=dict(cur.fetchall())
        cur.execute('SELECT user_id,numero FROM public.jogadores_numeros WHERE user_id=ANY(%s)',(ids,))
        numbers=dict(cur.fetchall())
    result=[]
    for member in members:
        uid=member['id'];previous=old.get(uid)
        try:
            new=clean_name(member['display_name'],numbers.get(uid),member['username'])
            status='sem_alteracao' if uid in old and new==previous else 'atualizar' if uid in old else 'criar_perfil'
        except ValueError:
            new=member['display_name'];status='nome_invalido'
        result.append({'user_id':uid,'old':previous,'new':new,'existed':uid in old,'status':status})
    return result

def apply(url,plan,guild_id,actor):
    results=[]
    with database(url) as cur:
        cur.execute(SCHEMA)
        for item in plan:
            row=dict(item)
            if row['status'] not in ('atualizar','criar_perfil'):
                results.append(row);continue
            if row['existed']:
                cur.execute('''UPDATE public.portal_perfis SET nome_publico=%s,atualizado_em=now()
                    WHERE user_id=%s AND nome_publico IS NOT DISTINCT FROM %s RETURNING user_id''',
                    (row['new'],row['user_id'],row['old']))
            else:
                cur.execute('''INSERT INTO public.portal_perfis(user_id,nome_publico) VALUES (%s,%s)
                    ON CONFLICT(user_id) DO NOTHING RETURNING user_id''',(row['user_id'],row['new']))
            if cur.fetchone():
                cur.execute('''INSERT INTO public.portal_nomes_importacoes_discord
                    (user_id,servidor_id,nome_anterior,nome_novo,importado_por) VALUES (%s,%s,%s,%s,%s)''',
                    (row['user_id'],guild_id,row['old'],row['new'],actor))
                row['status']='atualizado' if row['existed'] else 'perfil_criado'
            else:row['status']='alterado_entretanto_preservado'
            results.append(row)
    return results

def csv_report(rows):
    out=io.StringIO();writer=csv.writer(out)
    writer.writerow(['discord_user_id','nome_atual_site','nome_discord_sem_prefixo','resultado'])
    def safe(text):
        text='' if text is None else str(text)
        return "'"+text if text[:1] in ('=','+','-','@','\t','\r','\n') else text
    for r in rows:writer.writerow([str(r['user_id']),safe(r['old']),safe(r['new']),r['status']])
    return out.getvalue().encode('utf-8-sig')

def register(bot,database_url,channel_id):
    import discord
    from discord.ext import commands
    running=asyncio.Lock()

    @bot.command(name='nomes_site')
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def import_names(ctx,modo:str='verificar'):
        mode=modo.lower().strip()
        if ctx.channel.id!=channel_id:
            return await ctx.reply('Executa este comando no canal privado dos termos.',mention_author=False)
        if mode not in ('verificar','importar'):
            return await ctx.reply('Usa `!nomes_site verificar` ou `!nomes_site importar`.',mention_author=False)
        if running.locked():return await ctx.reply('Já existe uma importação/verificação de nomes em curso.',mention_author=False)
        async with running:
            status=await ctx.reply('A consultar todos os membros do servidor. Os bots serão excluídos.',mention_author=False)
            try:
                # Finish the complete member fetch before permitting any database writes.
                members=[]
                async for member in ctx.guild.fetch_members(limit=None):
                    if not member.bot:members.append({'id':member.id,'display_name':member.display_name,'username':member.name})
                plan=await asyncio.to_thread(preview,database_url,members)
                rows=plan if mode=='verificar' else await asyncio.to_thread(apply,database_url,plan,ctx.guild.id,ctx.author.id)
            except discord.Forbidden:
                return await status.edit(content='Sem acesso à lista de membros. Verifica o acesso do bot e o Server Members Intent. Nenhum nome foi importado.')
            except Exception as exc:
                return await status.edit(content='Não foi possível concluir ('+type(exc).__name__+'). Nenhuma alteração desta execução foi confirmada. Verifica o acesso aos membros e à base de dados.')
            counts=Counter(r['status'] for r in rows)
            summary='\n'.join('• '+name+': '+str(count) for name,count in sorted(counts.items())) or 'Nenhum membro encontrado.'
            await status.edit(content=('Verificação concluída — sem alterações no site.' if mode=='verificar' else 'Importação dos nomes concluída.')+'\n'+summary+'\nApenas o nome público é atualizado. Primeiro/último nome, avatares, termos, créditos e permissões mantêm-se.')
            await ctx.send(file=discord.File(io.BytesIO(csv_report(rows)),filename='relatorio_nomes_site.csv'),allowed_mentions=discord.AllowedMentions.none())
