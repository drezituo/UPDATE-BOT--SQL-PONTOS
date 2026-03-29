import discord
from discord.ext import commands, tasks
import psycopg2
import os
import asyncio
from datetime import datetime, timezone, timedelta

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

DB_ERROR_MSG = "✅ Bot ativado, volta a digitar o comando."

# ---------- INSCRIÇÕES ----------
LIMITE_PADRAO_INSCRICOES = 25
EMOJI_CONFIRMACAO = "✅"
HORAS_PAGAMENTO = 12
MINUTOS_AVISO = 30

# ---------- ROLE IDS (TIERS POR PONTOS NORMAIS) ----------
TIER_1_ROLE_ID = 1458650693316509718
TIER_2_ROLE_ID = 1463719829247885404
TIER_3_ROLE_ID = 1463723971068301446
TIER_4_ROLE_ID = 1463720049700372563
TIER_5_ROLE_ID = 1487161001752400074

TIER_ROLE_IDS = [
    TIER_1_ROLE_ID,
    TIER_2_ROLE_ID,
    TIER_3_ROLE_ID,
    TIER_4_ROLE_ID,
    TIER_5_ROLE_ID,
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE ----------
def get_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"Erro ao ligar à DB: {e}")
        return None

# ---------- TIER HELPERS ----------
def get_tier_from_points(valor: int) -> int:
    if valor <= 10:
        return 1
    elif valor <= 20:
        return 2
    elif valor <= 30:
        return 3
    elif valor <= 40:
        return 4
    else:
        return 5

def get_tier_role_id(tier: int) -> int:
    tier_map = {
        1: TIER_1_ROLE_ID,
        2: TIER_2_ROLE_ID,
        3: TIER_3_ROLE_ID,
        4: TIER_4_ROLE_ID,
        5: TIER_5_ROLE_ID,
    }
    return tier_map[tier]

async def update_member_tier_role(membro: discord.Member, pontos_normais: int):
    tier = get_tier_from_points(pontos_normais)
    correct_role_id = get_tier_role_id(tier)

    roles_to_remove = [role for role in membro.roles if role.id in TIER_ROLE_IDS and role.id != correct_role_id]
    if roles_to_remove:
        await membro.remove_roles(*roles_to_remove, reason="Atualização automática de tier por pontos")

    correct_role = membro.guild.get_role(correct_role_id)
    if correct_role and correct_role not in membro.roles:
        await membro.add_roles(correct_role, reason="Atualização automática de tier por pontos")

# ---------- ACTIVITY HELPERS ----------
def formatar_inatividade(dt):
    if not dt:
        return "Sem atividade registada"

    agora = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = agora - dt
    dias = diff.days
    segundos = diff.seconds
    horas = segundos // 3600
    minutos = (segundos % 3600) // 60

    if dias > 0:
        return f"{dias} dia(s)"
    if horas > 0:
        return f"{horas} hora(s)"
    return f"{minutos} minuto(s)"

# ---------- INSCRIÇÕES HELPERS ----------
def utc_now():
    return datetime.now(timezone.utc)

def is_thread_channel(channel):
    return isinstance(channel, discord.Thread)

async def apagar_mensagem_comando(ctx):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass

async def utilizador_e_admin_no_guild(guild: discord.Guild, user_id: int) -> bool:
    if guild is None:
        return False

    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    return member.guild_permissions.administrator

def obter_thread_config(thread_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()
    cursor.execute("""
        SELECT inscricoes_abertas, limite, thread_name
        FROM jogos_threads
        WHERE thread_id = %s
    """, (thread_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

def contar_inscricoes_validas(thread_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*)
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND estado IN ('pendente_pagamento', 'pago')
    """, (thread_id,))
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return total

async def criar_embed_inscricao_pendente(ctx, nome: str, expira_em: datetime):
    embed = discord.Embed(
        title=f"⚽ Inscrição - {ctx.channel.name}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="💬 Discord", value=ctx.author.mention, inline=True)
    embed.add_field(name="💰 Estado", value="⏳ Pendente de pagamento", inline=False)
    embed.add_field(name="⏰ Expira", value=f"<t:{int(expira_em.timestamp())}:F>", inline=True)
    embed.add_field(name="⌛ Tempo restante", value=f"<t:{int(expira_em.timestamp())}:R>", inline=True)
    embed.set_footer(text=f"⚡ Tens {HORAS_PAGAMENTO} horas para efetuar o pagamento")
    return embed

async def criar_embed_inscricao_paga(thread_name: str, nome: str, member_mention: str, avatar_url=None):
    embed = discord.Embed(
        title=f"⚽ Inscrição confirmada - {thread_name}",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="💬 Discord", value=member_mention, inline=True)
    embed.add_field(name="💰 Estado", value="✅ Pago", inline=False)
    embed.set_footer(text="✅ Pagamento confirmado")
    return embed

async def criar_embed_inscricao_expirada(thread_name: str, nome: str, member_mention: str, avatar_url=None):
    embed = discord.Embed(
        title=f"⚽ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="💬 Discord", value=member_mention, inline=True)
    embed.add_field(name="💰 Estado", value="❌ Expirada por falta de pagamento", inline=False)
    embed.set_footer(text="⌛ O prazo de pagamento terminou")
    return embed

async def criar_embed_inscricao_cancelada(thread_name: str, nome: str, member_mention: str, avatar_url=None, cancelado_por=""):
    embed = discord.Embed(
        title=f"⚽ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="💬 Discord", value=member_mention, inline=True)
    embed.add_field(name="💰 Estado", value="❌ Cancelada manualmente", inline=False)
    embed.add_field(name="🛑 Cancelado por", value=cancelado_por, inline=False)
    embed.set_footer(text="ℹ️ A vaga foi libertada")
    return embed

# ---------- CRIAR TABELAS ----------
conn = get_connection()
if conn:
    cursor = None
    try:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pontos (
            user_id BIGINT PRIMARY KEY,
            pontos INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        ALTER TABLE pontos
        ADD COLUMN IF NOT EXISTS ultima_atividade TIMESTAMP
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pontos_solo (
            user_id BIGINT PRIMARY KEY,
            pontos INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pontos_team (
            user_id BIGINT PRIMARY KEY,
            pontos INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pontos_tempo (
            user_id BIGINT PRIMARY KEY,
            pontos INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS jogos_threads (
            thread_id BIGINT PRIMARY KEY,
            thread_name TEXT NOT NULL,
            inscricoes_abertas BOOLEAN NOT NULL DEFAULT FALSE,
            limite INTEGER NOT NULL DEFAULT 25,
            criado_por BIGINT NOT NULL,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS inscricoes_jogos (
            id BIGSERIAL PRIMARY KEY,
            thread_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL UNIQUE,
            user_id BIGINT NOT NULL,
            nome_jogador TEXT NOT NULL,
            estado TEXT NOT NULL CHECK (estado IN ('pendente_pagamento', 'pago', 'expirado', 'cancelado')),
            criado_em TIMESTAMPTZ NOT NULL,
            expira_em TIMESTAMPTZ NOT NULL,
            aviso_30min_enviado BOOLEAN NOT NULL DEFAULT FALSE,
            confirmado_por BIGINT,
            confirmado_em TIMESTAMPTZ,
            UNIQUE(thread_id, user_id)
        )
        """)

        conn.commit()
        print("✅ Tabelas verificadas/criadas com sucesso.")

    except Exception as e:
        conn.rollback()
        print(f"❌ Erro ao criar tabelas: {e}")

    finally:
        if cursor:
            cursor.close()
        conn.close()
else:
    print("❌ Não foi possível ligar à base de dados para criar/verificar as tabelas.")

# ---------- EVENT ----------
@bot.event
async def on_ready():
    if not verificar_inscricoes.is_running():
        verificar_inscricoes.start()
    print(f"✅ Bot ligado como {bot.user}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Não tens permissão para usar este comando.", delete_after=10)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.command and ctx.command.name == "inscrever":
            await ctx.send("⚠️ Usa o comando assim: `!inscrever Nome Apelido`", delete_after=10)
            return

    print(f"Erro completo: {repr(error)}")
    try:
        await ctx.send(f"Erro: `{repr(error)}`", delete_after=15)
    except Exception:
        pass

# =========================
# 🆕 INSCRIÇÕES EM THREADS
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def abrir_inscricoes(ctx, limite: int = LIMITE_PADRAO_INSCRICOES):
    if not is_thread_channel(ctx.channel):
        return await ctx.send("⚠️ Este comando só pode ser usado dentro de uma thread.", delete_after=10)

    if limite <= 0:
        return await ctx.send("⚠️ O limite tem de ser superior a 0.", delete_after=10)

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jogos_threads (thread_id, thread_name, inscricoes_abertas, limite, criado_por)
        VALUES (%s, %s, TRUE, %s, %s)
        ON CONFLICT (thread_id)
        DO UPDATE SET
            thread_name = EXCLUDED.thread_name,
            inscricoes_abertas = TRUE,
            limite = EXCLUDED.limite,
            criado_por = EXCLUDED.criado_por
    """, (ctx.channel.id, ctx.channel.name, limite, ctx.author.id))
    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"✅ Inscrições abertas nesta thread.\n👥 Limite: **{limite} jogadores**")

@bot.command()
@commands.has_permissions(administrator=True)
async def fechar_inscricoes(ctx):
    if not is_thread_channel(ctx.channel):
        return await ctx.send("⚠️ Este comando só pode ser usado dentro de uma thread.", delete_after=10)

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        UPDATE jogos_threads
        SET inscricoes_abertas = FALSE
        WHERE thread_id = %s
    """, (ctx.channel.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send("🛑 Inscrições fechadas nesta thread.")

@bot.command()
@commands.has_permissions(administrator=True)
async def estado_inscricoes(ctx):
    if not is_thread_channel(ctx.channel):
        return await ctx.send("⚠️ Este comando só pode ser usado dentro de uma thread.", delete_after=10)

    dados = obter_thread_config(ctx.channel.id)
    if not dados:
        return await ctx.send("ℹ️ Esta thread ainda não foi configurada para inscrições.", delete_after=10)

    abertas, limite, _ = dados
    validas = contar_inscricoes_validas(ctx.channel.id) or 0
    vagas = max(limite - validas, 0)

    embed = discord.Embed(
        title="📋 Estado das Inscrições",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="🧵 Thread", value=ctx.channel.name, inline=False)
    embed.add_field(name="🔓 Estado", value="Abertas" if abertas else "Fechadas", inline=True)
    embed.add_field(name="👥 Limite", value=str(limite), inline=True)
    embed.add_field(name="✅ Válidas", value=str(validas), inline=True)
    embed.add_field(name="📉 Vagas restantes", value=str(vagas), inline=False)

    await ctx.send(embed=embed)

@bot.command()
async def inscrever(ctx, *, nome: str):
    nome = nome.strip()

    if not is_thread_channel(ctx.channel):
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Este comando só pode ser usado dentro da thread do jogo.", delete_after=10)

    if len(nome) < 3:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Nome inválido.", delete_after=10)

    dados = obter_thread_config(ctx.channel.id)
    if not dados:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ As inscrições não estão ativas nesta thread.", delete_after=10)

    abertas, limite, _ = dados

    if not abertas:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("🛑 As inscrições estão fechadas nesta thread.", delete_after=10)

    validas = contar_inscricoes_validas(ctx.channel.id)
    if validas is None:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(DB_ERROR_MSG)

    if validas >= limite:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(f"⚠️ Este jogo já atingiu o limite de **{limite} jogadores**.", delete_after=10)

    conn = get_connection()
    if not conn:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT estado
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND user_id = %s
          AND estado IN ('pendente_pagamento', 'pago')
    """, (ctx.channel.id, ctx.author.id))
    existente = cursor.fetchone()

    if existente:
        estado = existente[0]
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)

        if estado == "pendente_pagamento":
            return await ctx.send("⚠️ Já estás inscrito. Falta apenas confirmar o pagamento.", delete_after=10)
        return await ctx.send("✅ Já estás confirmado neste jogo.", delete_after=10)

    criado_em = utc_now()
    expira_em = criado_em + timedelta(hours=HORAS_PAGAMENTO)

    embed = await criar_embed_inscricao_pendente(ctx, nome, expira_em)
    msg = await ctx.channel.send(embed=embed)

    try:
        cursor.execute("""
            INSERT INTO inscricoes_jogos (
                thread_id, message_id, user_id, nome_jogador,
                estado, criado_em, expira_em, aviso_30min_enviado
            )
            VALUES (%s, %s, %s, %s, 'pendente_pagamento', %s, %s, FALSE)
        """, (
            ctx.channel.id,
            msg.id,
            ctx.author.id,
            nome,
            criado_em,
            expira_em
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Já tens uma inscrição ativa neste jogo.", delete_after=10)

    cursor.close()
    conn.close()
    await apagar_mensagem_comando(ctx)

@bot.command()
async def cancelarinscricao(ctx, membro: discord.Member = None):
    if not is_thread_channel(ctx.channel):
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Este comando só pode ser usado dentro da thread do jogo.", delete_after=10)

    membro = membro or ctx.author
    is_admin = ctx.author.guild_permissions.administrator

    if not is_admin and membro.id != ctx.author.id:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("❌ Só podes cancelar a tua própria inscrição.", delete_after=10)

    conn = get_connection()
    if not conn:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, message_id, nome_jogador, estado
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND user_id = %s
          AND estado IN ('pendente_pagamento', 'pago')
    """, (ctx.channel.id, membro.id))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Não existe nenhuma inscrição ativa para esse jogador nesta thread.", delete_after=10)

    inscricao_id, message_id, nome_jogador, estado = row

    cursor.execute("""
        UPDATE inscricoes_jogos
        SET estado = 'cancelado'
        WHERE id = %s
    """, (inscricao_id,))
    conn.commit()
    cursor.close()
    conn.close()

    avatar_url = membro.display_avatar.url if membro else None
    member_mention = membro.mention

    if ctx.author.id == membro.id:
        cancelado_por = ctx.author.mention
    else:
        cancelado_por = f"{ctx.author.mention} (admin)"

    try:
        msg = await ctx.channel.fetch_message(message_id)
        embed = await criar_embed_inscricao_cancelada(
            ctx.channel.name,
            nome_jogador,
            member_mention,
            avatar_url,
            cancelado_por
        )
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    await apagar_mensagem_comando(ctx)

    if ctx.author.id == membro.id:
        await ctx.send(f"✅ {membro.mention}, a tua inscrição foi cancelada.", delete_after=10)
    else:
        await ctx.send(f"✅ A inscrição de {membro.mention} foi cancelada por {ctx.author.mention}.", delete_after=10)

    if ctx.author.id != membro.id:
        try:
            await membro.send(f"❌ A tua inscrição na thread **{ctx.channel.name}** foi cancelada por um admin.")
        except (discord.Forbidden, discord.HTTPException):
            pass

# =========================
# 🔥 SISTEMA ANTIGO
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def addpontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    if resultado:
        novo_total = resultado[0] + quantidade
        cursor.execute(
            "UPDATE pontos SET pontos = %s, ultima_atividade = NOW() WHERE user_id = %s",
            (novo_total, membro.id)
        )
    else:
        novo_total = quantidade
        cursor.execute(
            "INSERT INTO pontos (user_id, pontos, ultima_atividade) VALUES (%s, %s, NOW())",
            (membro.id, quantidade)
        )

    conn.commit()
    cursor.close()
    conn.close()

    try:
        await update_member_tier_role(membro, novo_total)
    except Exception as e:
        print(f"Erro ao atualizar cargo de tier: {e}")

    await ctx.send(f"✅ {membro.display_name} agora tem **{novo_total} pontos**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removepontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    if not resultado:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Esse usuário não tem pontos.")

    novo_total = max(resultado[0] - quantidade, 0)
    cursor.execute("UPDATE pontos SET pontos = %s WHERE user_id = %s", (novo_total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    try:
        await update_member_tier_role(membro, novo_total)
    except Exception as e:
        print(f"Erro ao atualizar cargo de tier: {e}")

    await ctx.send(f"❌ {membro.display_name} agora tem **{novo_total} pontos**")

@bot.command()
async def pontos(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    total = resultado[0] if resultado else 0

    cursor.close()
    conn.close()

    await ctx.send(f"⭐ {membro.display_name} tem **{total} pontos**")

# =========================
# 🆕 RANKING PONTOS NORMAL
# =========================
@bot.command()
async def ranking(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos ORDER BY pontos DESC")
    dados = cursor.fetchall()

    if not dados:
        await ctx.send("⚠️ Nenhum ponto registrado ainda.")
        cursor.close()
        conn.close()
        return

    msg = "**🏆 Ranking de Pontos:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        membro = ctx.guild.get_member(uid)
        nome = membro.display_name if membro else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} pontos\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()

# =========================
# 🆕 SOLO REBIRTH
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def addsolo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = (r[0] if r else 0) + quantidade

    if r:
        cursor.execute("UPDATE pontos_solo SET pontos = %s WHERE user_id = %s", (total, membro.id))
    else:
        cursor.execute("INSERT INTO pontos_solo VALUES (%s, %s)", (membro.id, total))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"🔥 {membro.display_name} agora tem **{total} vitórias no Solo Rebirth**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removesolo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    if not r:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Sem dados.")

    total = max(r[0] - quantidade, 0)

    cursor.execute("UPDATE pontos_solo SET pontos = %s WHERE user_id = %s", (total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} vitórias no Solo Rebirth**")

@bot.command()
async def pontossolo(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = r[0] if r else 0

    cursor.close()
    conn.close()

    await ctx.send(f"🎯 {membro.display_name} tem **{total} vitórias no Solo Rebirth!**")

@bot.command()
async def rankingsolo(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_solo ORDER BY pontos DESC")
    dados = cursor.fetchall()

    msg = "**🏆 Ranking Solo Rebirth:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        m = ctx.guild.get_member(uid)
        nome = m.display_name if m else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} vitórias\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()

# =========================
# 🆕 TEAM REBIRTH
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def addteam(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = (r[0] if r else 0) + quantidade

    if r:
        cursor.execute("UPDATE pontos_team SET pontos = %s WHERE user_id = %s", (total, membro.id))
    else:
        cursor.execute("INSERT INTO pontos_team VALUES (%s, %s)", (membro.id, total))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"👥 {membro.display_name} agora tem **{total} vitórias no Team Rebirth**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removeteam(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    if not r:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Sem dados.")

    total = max(r[0] - quantidade, 0)

    cursor.execute("UPDATE pontos_team SET pontos = %s WHERE user_id = %s", (total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} vitórias no Team Rebirth**")

@bot.command()
async def pontosteam(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = r[0] if r else 0

    cursor.close()
    conn.close()

    await ctx.send(f"👥 {membro.display_name} tem **{total} vitórias no Team Rebirth!**")

@bot.command()
async def rankingteam(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_team ORDER BY pontos DESC")
    dados = cursor.fetchall()

    msg = "**🏆 Ranking Team Rebirth:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        m = ctx.guild.get_member(uid)
        nome = m.display_name if m else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} vitórias\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()

# =========================
# 🆕 TEMPO EM PISTA
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def addtempo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = (r[0] if r else 0) + quantidade

    if r:
        cursor.execute("UPDATE pontos_tempo SET pontos = %s WHERE user_id = %s", (total, membro.id))
    else:
        cursor.execute("INSERT INTO pontos_tempo VALUES (%s, %s)", (membro.id, total))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"⏱️ {membro.display_name} agora tem **{total} tempo em pista**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removetempo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    if not r:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Sem dados.")

    total = max(r[0] - quantidade, 0)

    cursor.execute("UPDATE pontos_tempo SET pontos = %s WHERE user_id = %s", (total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} tempo em pista**")

@bot.command()
async def tempopista(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = r[0] if r else 0

    cursor.close()
    conn.close()

    await ctx.send(f"⏱️ {membro.display_name} tem **{total} tempo em pista**")

@bot.command()
async def rankingtempo(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_tempo ORDER BY pontos DESC")
    dados = cursor.fetchall()

    if not dados:
        await ctx.send("⚠️ Nenhum tempo em pista registrado ainda.")
        cursor.close()
        conn.close()
        return

    msg = "**⏱️ Ranking Tempo em Pista:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        m = ctx.guild.get_member(uid)
        nome = m.display_name if m else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} tempo em pista\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()

# =========================
# 🆕 STATUS (COM TIERS + INATIVIDADE + TEMPO)
# =========================
@bot.command()
async def status(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    # ---------- PONTOS NORMAIS ----------
    cursor.execute("SELECT pontos, ultima_atividade FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0
    ultima_atividade = r[1] if r else None

    cursor.execute("SELECT user_id FROM pontos ORDER BY pontos DESC")
    ranking_dados = [uid for (uid,) in cursor.fetchall()]
    rank_pontos = ranking_dados.index(membro.id) + 1 if membro.id in ranking_dados else "N/A"

    # ---------- SOLO ----------
    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_solo = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_solo ORDER BY pontos DESC")
    ranking_dados_solo = [uid for (uid,) in cursor.fetchall()]
    rank_solo = ranking_dados_solo.index(membro.id) + 1 if membro.id in ranking_dados_solo else "N/A"

    # ---------- TEAM ----------
    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_team = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_team ORDER BY pontos DESC")
    ranking_dados_team = [uid for (uid,) in cursor.fetchall()]
    rank_team = ranking_dados_team.index(membro.id) + 1 if membro.id in ranking_dados_team else "N/A"

    # ---------- TEMPO EM PISTA ----------
    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_tempo = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_tempo ORDER BY pontos DESC")
    ranking_dados_tempo = [uid for (uid,) in cursor.fetchall()]
    rank_tempo = ranking_dados_tempo.index(membro.id) + 1 if membro.id in ranking_dados_tempo else "N/A"

    cursor.close()
    conn.close()

    def get_tier_data(valor):
        if valor <= 10:
            tier = 1
        elif valor <= 20:
            tier = 2
        elif valor <= 30:
            tier = 3
        elif valor <= 40:
            tier = 4
        else:
            tier = 5

        if tier == 1:
            progresso = valor
        elif tier == 2:
            progresso = valor - 10
        elif tier == 3:
            progresso = valor - 20
        elif tier == 4:
            progresso = valor - 30
        else:
            progresso = valor - 40

        if progresso > 10:
            progresso = 10
        if progresso < 0:
            progresso = 0

        emojis_tier = {
            1: ("🟩", "⬛", "BRONZE"),
            2: ("🟦", "⬛", "PRATA"),
            3: ("🟨", "⬛", "OURO"),
            4: ("🟧", "⬛", "PLATINA"),
            5: ("🟥", "⬛", "DIAMANTE"),
        }

        cheio, vazio, nome_tier = emojis_tier[tier]
        barra = cheio * progresso + vazio * (10 - progresso)

        return tier, nome_tier, barra, progresso

    tier_pontos, nome_tier_pontos, barra_pontos, prog_pontos = get_tier_data(pontos)
    tier_solo, nome_tier_solo, barra_solo, prog_solo = get_tier_data(pontos_solo)
    tier_team, nome_tier_team, barra_team, prog_team = get_tier_data(pontos_team)

    tier_roles = {
        1: TIER_1_ROLE_ID,
        2: TIER_2_ROLE_ID,
        3: TIER_3_ROLE_ID,
        4: TIER_4_ROLE_ID,
        5: TIER_5_ROLE_ID,
    }

    cor = discord.Color.blurple()

    for role in membro.roles:
        for _, role_id in tier_roles.items():
            if role.id == role_id:
                if role.color.value != 0:
                    cor = role.color
                break

    embed = discord.Embed(
        title="🎮┃PERFIL DE JOGADOR",
        description=f"**{membro.display_name}**",
        color=cor
    )

    embed.set_thumbnail(url=membro.display_avatar.url)

    embed.add_field(
        name="⭐ PRESENÇAS",
        value=(
            f"**Tier {tier_pontos} • {nome_tier_pontos}**\n"
            f"{barra_pontos}\n"
            f"`{prog_pontos}/10` no tier atual • 🏅 `#{rank_pontos}`\n"
            f"Total: **{pontos:02} presenças**"
        ),
        inline=False
    )

    embed.add_field(
        name="🔥 SOLO REBIRTH",
        value=(
            f"**Tier {tier_solo} • {nome_tier_solo}**\n"
            f"{barra_solo}\n"
            f"`{prog_solo}/10` no tier atual • 🏅 `#{rank_solo}`\n"
            f"Total: **{pontos_solo:02} vitórias**"
        ),
        inline=False
    )

    embed.add_field(
        name="👥 TEAM REBIRTH",
        value=(
            f"**Tier {tier_team} • {nome_tier_team}**\n"
            f"{barra_team}\n"
            f"`{prog_team}/10` no tier atual • 🏅 `#{rank_team}`\n"
            f"Total: **{pontos_team:02} vitórias**"
        ),
        inline=False
    )

    embed.add_field(
        name="⏱️ TEMPO EM PISTA",
        value=(
            f"🏅 Ranking: `#{rank_tempo}`\n"
            f"Total: **{pontos_tempo:02}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🕒 ATIVIDADE",
        value=(
            f"Inativo há: **{formatar_inatividade(ultima_atividade)}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🏆 RESUMO",
        value=(
            f"🎯 Total vitórias: **{pontos_solo + pontos_team}**\n"
            f"📊 Participações: **{pontos}**"
        ),
        inline=False
    )

    embed.set_footer(text="⚡ Sistema competitivo ativo • 5 Tiers")
    embed.timestamp = discord.utils.utcnow()

    await ctx.send(embed=embed)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != EMOJI_CONFIRMACAO:
        return

    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    if not await utilizador_e_admin_no_guild(guild, payload.user_id):
        return

    conn = get_connection()
    if not conn:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT thread_id, user_id, nome_jogador, estado
        FROM inscricoes_jogos
        WHERE message_id = %s
    """, (payload.message_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return

    thread_id, user_id, nome_jogador, estado = row

    if estado != "pendente_pagamento":
        cursor.close()
        conn.close()
        return

    cursor.execute("""
        UPDATE inscricoes_jogos
        SET estado = 'pago',
            confirmado_por = %s,
            confirmado_em = NOW()
        WHERE message_id = %s
    """, (payload.user_id, payload.message_id))
    conn.commit()
    cursor.close()
    conn.close()

    canal = bot.get_channel(thread_id)
    user = bot.get_user(user_id)

    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            user = None

    avatar_url = None
    if user:
        avatar_url = user.display_avatar.url
    elif guild.icon:
        avatar_url = guild.icon.url

    member_mention = f"<@{user_id}>"

    if canal:
        try:
            msg = await canal.fetch_message(payload.message_id)
            embed = await criar_embed_inscricao_paga(canal.name, nome_jogador, member_mention, avatar_url)
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    if user:
        try:
            await user.send(f"✅ O teu pagamento foi confirmado na thread **{canal.name if canal else 'do jogo'}**.")
        except (discord.Forbidden, discord.HTTPException):
            pass

@tasks.loop(minutes=1)
async def verificar_inscricoes():
    conn = get_connection()
    if not conn:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, thread_id, message_id, user_id, nome_jogador, expira_em, aviso_30min_enviado
        FROM inscricoes_jogos
        WHERE estado = 'pendente_pagamento'
    """)
    rows = cursor.fetchall()

    agora = utc_now()

    for inscricao_id, thread_id, message_id, user_id, nome_jogador, expira_em, aviso_30min_enviado in rows:
        if expira_em.tzinfo is None:
            expira_em = expira_em.replace(tzinfo=timezone.utc)

        tempo_restante = expira_em - agora

        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                user = None

        if (not aviso_30min_enviado) and timedelta(minutes=0) < tempo_restante <= timedelta(minutes=MINUTOS_AVISO):
            if user:
                try:
                    await user.send(
                        "⏰ Faltam menos de 30 minutos para a tua inscrição expirar.\n"
                        "Se o pagamento não for confirmado a tempo, a inscrição será cancelada."
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

            cursor.execute("""
                UPDATE inscricoes_jogos
                SET aviso_30min_enviado = TRUE
                WHERE id = %s
            """, (inscricao_id,))
            conn.commit()

        if agora >= expira_em:
            cursor.execute("""
                UPDATE inscricoes_jogos
                SET estado = 'expirado'
                WHERE id = %s
            """, (inscricao_id,))
            conn.commit()

            canal = bot.get_channel(thread_id)
            avatar_url = user.display_avatar.url if user else None
            member_mention = f"<@{user_id}>"

            if canal:
                try:
                    msg = await canal.fetch_message(message_id)
                    embed = await criar_embed_inscricao_expirada(canal.name, nome_jogador, member_mention, avatar_url)
                    await msg.edit(embed=embed)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            if user:
                try:
                    await user.send(f"❌ A tua inscrição expirou porque passaram {HORAS_PAGAMENTO} horas sem confirmação de pagamento.")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    cursor.close()
    conn.close()

# ---------- AUTO RESTART ----------
async def start_bot():
    while True:
        try:
            await bot.start(TOKEN)
        except Exception as e:
            print(f"Erro crítico: {e}")
            await asyncio.sleep(5)

asyncio.run(start_bot())
