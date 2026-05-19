import discord
from discord.ext import commands, tasks
import asyncio
import asyncio
import psycopg2
import os
import random
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


def normalizar_nome(nome: str) -> str:
    return " ".join(nome.split()).strip()


def extrair_jogador_da_inscricao(ctx, nome: str):
    nome = normalizar_nome(nome)

    if ctx.message.mentions:
        membro_mencionado = ctx.message.mentions[0]
        return membro_mencionado.display_name, membro_mencionado

    return nome, None


def extrair_nome_cancelamento(ctx, nome: str):
    nome = normalizar_nome(nome)

    if ctx.message.mentions:
        membro_mencionado = ctx.message.mentions[0]
        return membro_mencionado.display_name, membro_mencionado

    return nome, None


async def encontrar_membro_por_nome(guild: discord.Guild, nome_jogador: str):
    if guild is None:
        return None

    nome_jogador = normalizar_nome(nome_jogador).lower()

    def corresponde(member: discord.Member) -> bool:
        candidatos = {
            normalizar_nome(member.display_name).lower(),
            normalizar_nome(member.name).lower(),
        }

        global_name = getattr(member, "global_name", None)
        if global_name:
            candidatos.add(normalizar_nome(global_name).lower())

        return nome_jogador in candidatos

    for member in guild.members:
        if corresponde(member):
            return member

    try:
        async for member in guild.fetch_members(limit=None):
            if corresponde(member):
                return member
    except (discord.Forbidden, discord.HTTPException):
        pass

    return None


async def obter_avatar_url_jogador(guild: discord.Guild, nome_jogador: str, membro: discord.Member = None):
    if membro is not None:
        return membro.display_avatar.url

    membro_encontrado = await encontrar_membro_por_nome(guild, nome_jogador)
    if membro_encontrado:
        return membro_encontrado.display_avatar.url

    return "https://cdn.discordapp.com/embed/avatars/0.png"


async def obter_valor_jogador_embed(guild: discord.Guild, nome_jogador: str, membro: discord.Member = None):
    if membro is not None:
        return membro.mention

    membro_encontrado = await encontrar_membro_por_nome(guild, nome_jogador)
    if membro_encontrado:
        return membro_encontrado.mention

    return nome_jogador


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
        SELECT inscricoes_abertas, limite, thread_name, estado_msg_id
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


# ---------- EQUIPAS HELPERS ----------
def calcular_pontos_jogador_sync(user_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (user_id,))
    r = cursor.fetchone()
    solo = r[0] if r else 0

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (user_id,))
    r = cursor.fetchone()
    team = r[0] if r else 0

    cursor.close()
    conn.close()

    total = solo + team

    return {
        "solo": solo,
        "team": team,
        "total": total,
    }


def calcular_pontos_equipa_sync(equipa_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id
        FROM equipas_membros
        WHERE equipa_id = %s
    """, (equipa_id,))
    membros = cursor.fetchall()

    totais = {
        "solo": 0,
        "team": 0,
        "total": 0,
        "membros": len(membros),
    }

    for (user_id,) in membros:
        cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (user_id,))
        r = cursor.fetchone()
        solo = r[0] if r else 0

        cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (user_id,))
        r = cursor.fetchone()
        team = r[0] if r else 0

        totais["solo"] += solo
        totais["team"] += team
        totais["total"] += solo + team

    cursor.close()
    conn.close()
    return totais

# ---------- EMBEDS INSCRIÇÕES ----------
async def criar_embed_inscricao_pendente(ctx, nome: str, expira_em: datetime, membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(ctx.guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(ctx.guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição - {ctx.channel.name}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=ctx.author.mention, inline=True)
    embed.add_field(name="📌 Estado", value="⏳ Inscrição por finalizar", inline=False)
    embed.add_field(name="⏰ Expira", value=f"<t:{int(expira_em.timestamp())}:F>", inline=True)
    embed.add_field(name="⌛ Tempo restante", value=f"<t:{int(expira_em.timestamp())}:R>", inline=True)
    embed.set_footer(text=f"⚡ Tens {HORAS_PAGAMENTO} horas para concluir a inscrição")
    return embed


async def criar_embed_inscricao_paga(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str, membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição confirmada - {thread_name}",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="✅ Inscrição finalizada", inline=False)
    embed.set_footer(text="✅ Inscrição concluída com sucesso")
    return embed


async def criar_embed_inscricao_expirada(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str, membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="❌ Inscrição expirada por falta de finalização", inline=False)
    embed.set_footer(text="⌛ O prazo para finalizar a inscrição terminou")
    return embed


async def criar_embed_inscricao_cancelada(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str, cancelado_por="", membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="❌ Inscrição cancelada manualmente", inline=False)
    embed.add_field(name="🛑 Cancelado por", value=cancelado_por, inline=False)
    embed.set_footer(text="ℹ️ A vaga foi libertada")
    return embed


async def atualizar_embed_estado(channel: discord.Thread):
    conn = get_connection()
    if not conn:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT inscricoes_abertas, limite, estado_msg_id
        FROM jogos_threads
        WHERE thread_id = %s
    """, (channel.id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return

    abertas, limite, estado_msg_id = row

    cursor.execute("""
        SELECT COUNT(*)
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND estado IN ('pendente_pagamento', 'pago')
    """, (channel.id,))
    total = cursor.fetchone()[0]

    embed = discord.Embed(
        title="📋 Estado das Inscrições",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="🔓 Estado", value="Abertas" if abertas else "Fechadas", inline=True)
    embed.add_field(name="✅ Inscrições ativas", value=str(total), inline=True)
    embed.add_field(name="📉 Vagas restantes", value=str(max(limite - total, 0)), inline=True)

    if estado_msg_id:
        try:
            old_msg = await channel.fetch_message(estado_msg_id)
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    try:
        new_msg = await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        cursor.close()
        conn.close()
        return

    cursor.execute("""
        UPDATE jogos_threads
        SET estado_msg_id = %s
        WHERE thread_id = %s
    """, (new_msg.id, channel.id))
    conn.commit()
    cursor.close()
    conn.close()


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
            criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            estado_msg_id BIGINT
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
            confirmado_em TIMESTAMPTZ
        )
        """)

        cursor.execute("""
        ALTER TABLE jogos_threads
        ADD COLUMN IF NOT EXISTS estado_msg_id BIGINT
        """)

        cursor.execute("""
        ALTER TABLE inscricoes_jogos
        DROP CONSTRAINT IF EXISTS inscricoes_jogos_thread_id_user_id_key
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipas (
            id BIGSERIAL PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE,
            logo_url TEXT,
            criado_por BIGINT NOT NULL,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipas_membros (
            equipa_id BIGINT NOT NULL REFERENCES equipas(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            PRIMARY KEY (equipa_id, user_id)
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
    print("========== BOT READY ==========")
    print(f"✅ Bot ligado como {bot.user}")
    print(f"message_content: {bot.intents.message_content}")
    print(f"members: {bot.intents.members}")
    print(f"guilds: {bot.intents.guilds}")
    print("===============================")

    if not verificar_inscricoes.is_running():
        verificar_inscricoes.start()

    if not limpar_timers_auto.is_running():
        limpar_timers_auto.start()


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    print(f"[MSG] {message.author} em #{message.channel}: {message.content}")
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Não tens permissão para usar este comando.", delete_after=10)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.command and ctx.command.name in (
            "inscrever", "cancelarinscricao", "criarequipa", "adicionarmembroequipa",
            "removermembroequipa", "equipa", "apagarequipa", "mudarlogoequipa"
        ):
            await ctx.send("⚠️ Faltam argumentos no comando.", delete_after=10)
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
async def ping(ctx):
    await ctx.send("pong")


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
    await atualizar_embed_estado(ctx.channel)


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
    await atualizar_embed_estado(ctx.channel)


@bot.command()
@commands.has_permissions(administrator=True)
async def estado_inscricoes(ctx):
    if not is_thread_channel(ctx.channel):
        return await ctx.send("⚠️ Este comando só pode ser usado dentro de uma thread.", delete_after=10)

    dados = obter_thread_config(ctx.channel.id)
    if not dados:
        return await ctx.send("ℹ️ Esta thread ainda não foi configurada para inscrições.", delete_after=10)

    await atualizar_embed_estado(ctx.channel)


@bot.command()
async def inscrever(ctx, *, nome: str):
    nome_original = nome
    nome, membro_jogador = extrair_jogador_da_inscricao(ctx, nome_original)

    if not is_thread_channel(ctx.channel):
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Este comando só pode ser usado dentro da thread do jogo.", delete_after=10)

    if len(normalizar_nome(nome)) < 3:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Nome inválido.", delete_after=10)

    dados = obter_thread_config(ctx.channel.id)
    if not dados:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ As inscrições não estão ativas nesta thread.", delete_after=10)

    abertas, limite, _, _ = dados

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
        SELECT 1
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND LOWER(nome_jogador) = LOWER(%s)
          AND estado IN ('pendente_pagamento', 'pago')
    """, (ctx.channel.id, nome))
    existe_nome = cursor.fetchone()

    if existe_nome:
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Já existe uma inscrição com esse nome neste jogo.", delete_after=10)

    criado_em = utc_now()
    expira_em = criado_em + timedelta(hours=HORAS_PAGAMENTO)

    embed = await criar_embed_inscricao_pendente(ctx, nome, expira_em, membro_jogador)
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
    except Exception as e:
        conn.rollback()
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        print(f"Erro ao inserir inscrição: {e}")
        return await ctx.send("⚠️ Erro ao criar a inscrição.", delete_after=10)

    cursor.close()
    conn.close()
    await apagar_mensagem_comando(ctx)
    await atualizar_embed_estado(ctx.channel)


@bot.command()
async def cancelarinscricao(ctx, *, nome: str):
    if not is_thread_channel(ctx.channel):
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Este comando só pode ser usado dentro da thread do jogo.", delete_after=10)

    nome, membro_jogador = extrair_nome_cancelamento(ctx, nome)

    if len(nome) < 3:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Nome inválido.", delete_after=10)

    is_admin = ctx.author.guild_permissions.administrator

    conn = get_connection()
    if not conn:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()

    if is_admin:
        cursor.execute("""
            SELECT id, message_id, nome_jogador, user_id
            FROM inscricoes_jogos
            WHERE thread_id = %s
              AND LOWER(nome_jogador) = LOWER(%s)
              AND estado IN ('pendente_pagamento', 'pago')
            ORDER BY id DESC
            LIMIT 1
        """, (ctx.channel.id, nome))
    else:
        cursor.execute("""
            SELECT id, message_id, nome_jogador, user_id
            FROM inscricoes_jogos
            WHERE thread_id = %s
              AND LOWER(nome_jogador) = LOWER(%s)
              AND user_id = %s
              AND estado IN ('pendente_pagamento', 'pago')
            ORDER BY id DESC
            LIMIT 1
        """, (ctx.channel.id, nome, ctx.author.id))

    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        if is_admin:
            return await ctx.send("⚠️ Não existe nenhuma inscrição ativa com esse nome nesta thread.", delete_after=10)
        return await ctx.send("⚠️ Não encontrei uma inscrição ativa tua com esse nome nesta thread.", delete_after=10)

    inscricao_id, message_id, nome_jogador, user_id = row

    cursor.execute("""
        UPDATE inscricoes_jogos
        SET estado = 'cancelado'
        WHERE id = %s
    """, (inscricao_id,))
    conn.commit()
    cursor.close()
    conn.close()

    user = ctx.guild.get_member(user_id)
    if user is None:
        try:
            user = await ctx.guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            user = None

    autor_inscricao_mention = f"<@{user_id}>"

    if ctx.author.id == user_id:
        cancelado_por = ctx.author.mention
    else:
        cancelado_por = f"{ctx.author.mention} (admin)"

    try:
        msg = await ctx.channel.fetch_message(message_id)
        embed = await criar_embed_inscricao_cancelada(
            ctx.guild,
            ctx.channel.name,
            nome_jogador,
            autor_inscricao_mention,
            cancelado_por,
            membro_jogador
        )
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    await apagar_mensagem_comando(ctx)

    if ctx.author.id == user_id:
        await ctx.send(f"✅ A inscrição de **{nome_jogador}** foi cancelada.", delete_after=10)
    else:
        await ctx.send(f"✅ A inscrição de **{nome_jogador}** foi cancelada por {ctx.author.mention}.", delete_after=10)

    if user and ctx.author.id != user_id:
        try:
            await user.send(f"❌ A inscrição de **{nome_jogador}** na thread **{ctx.channel.name}** foi cancelada por um admin.")
        except (discord.Forbidden, discord.HTTPException):
            pass

    await atualizar_embed_estado(ctx.channel)




# =========================
# 📜 LISTA DE COMANDOS
# =========================
@bot.command()
async def comandos(ctx):
    embed = discord.Embed(
        title="📜 Lista de Comandos",
        description="Aqui estão todos os comandos disponíveis no bot:",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="🎟️ Inscrições",
        value=(
            "`!abrir_inscricoes [limite]`\n"
            "`!fechar_inscricoes`\n"
            "`!estado_inscricoes`\n"
            "`!inscrever nome`\n"
            "`!cancelarinscricao nome`"
        ),
        inline=False
    )

    embed.add_field(
        name="⭐ Pontos / Presenças",
        value=(
            "`!addpontos @user quantidade`\n"
            "`!removepontos @user quantidade`\n"
            "`!pontos [@user]`\n"
            "`!ranking`"
        ),
        inline=False
    )

    embed.add_field(
        name="🔥 Solo Rebirth",
        value=(
            "`!addsolo @user quantidade`\n"
            "`!removesolo @user quantidade`\n"
            "`!pontossolo [@user]`\n"
            "`!rankingsolo`"
        ),
        inline=False
    )

    embed.add_field(
        name="👥 Team Win",
        value=(
            "`!addteam @user quantidade`\n"
            "`!removeteam @user quantidade`\n"
            "`!pontosteam [@user]`\n"
            "`!rankingteam`"
        ),
        inline=False
    )

    embed.add_field(
        name="⏱️ Tempo em Pista",
        value=(
            "`!addtempo @user quantidade`\n"
            "`!removetempo @user quantidade`\n"
            "`!tempopista [@user]`\n"
            "`!rankingtempo`"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Equipas",
        value=(
            "`!criarequipa nome [logo_url]`\n"
            "`!adicionarmembroequipa nome @user`\n"
            "`!removermembroequipa nome @user`\n"
            "`!mudarlogoequipa nome url`\n"
            "`!apagarequipa nome`\n"
            "`!equipa nome`\n"
            "`!rankingequipas`\n"
            "`!listarequipas`"
        ),
        inline=False
    )

    embed.add_field(
        name="🎮 Perfil / Utilidades",
        value=(
            "`!status [@user]`\n"
            "`!ping`\n"
            "`!comandos`"
        ),
        inline=False
    )

    embed.add_field(
        name="🎖️ Milsim — Operação Duality",
        value=(
            "`!start_op`\n"
            "`!codigo CÓDIGO`\n"
            "`!reagrupado`\n"
            "`!capturar player_id`\n"
            "`!opstatus`\n"
            "`!score`\n"
            "`!painel_op`\n"
            "`!gm_blackout`\n"
            "`!gm_next`\n"
            "`!gm_end`"
        ),
        inline=False
    )


    embed.add_field(
        name="🔐 Nota",
        value="Comandos de adicionar/remover pontos, inscrições admin e gestão de equipas precisam de permissão de administrador.",
        inline=False
    )

    embed.set_footer(text="⚡ Prefixo do bot: !")

    await ctx.send(embed=embed)


# =========================
# 🛡️ SISTEMA DE EQUIPAS
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def criarequipa(ctx, nome: str, logo_url: str = None):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO equipas (nome, logo_url, criado_por)
            VALUES (%s, %s, %s)
        """, (nome, logo_url, ctx.author.id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        print(f"Erro ao criar equipa: {e}")
        return await ctx.send("⚠️ Já existe uma equipa com esse nome ou ocorreu um erro.")

    cursor.close()
    conn.close()

    embed = discord.Embed(
        title="🛡️ Nova Equipa Criada",
        description=f"A equipa **{nome}** foi criada com sucesso!",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    if logo_url:
        embed.set_thumbnail(url=logo_url)
    embed.add_field(name="👑 Criada por", value=ctx.author.mention, inline=True)
    embed.add_field(name="👥 Membros", value="Nenhum membro ainda", inline=True)
    embed.set_footer(text="Sistema competitivo de equipas ativo")

    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def adicionarmembroequipa(ctx, nome_equipa: str, membro: discord.Member):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome
        FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    equipa = cursor.fetchone()

    if not equipa:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Essa equipa não existe.")

    equipa_id, nome = equipa

    try:
        cursor.execute("""
            INSERT INTO equipas_membros (equipa_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (equipa_id, membro.id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        print(f"Erro ao adicionar membro à equipa: {e}")
        return await ctx.send("⚠️ Erro ao adicionar membro à equipa.")

    cursor.close()
    conn.close()

    await ctx.send(f"✅ {membro.mention} foi adicionado à equipa **{nome}**.")


@bot.command()
@commands.has_permissions(administrator=True)
async def removermembroequipa(ctx, nome_equipa: str, membro: discord.Member):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome
        FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    equipa = cursor.fetchone()

    if not equipa:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Essa equipa não existe.")

    equipa_id, nome = equipa

    cursor.execute("""
        DELETE FROM equipas_membros
        WHERE equipa_id = %s AND user_id = %s
    """, (equipa_id, membro.id))
    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.mention} foi removido da equipa **{nome}**.")


@bot.command()
@commands.has_permissions(administrator=True)
async def mudarlogoequipa(ctx, nome_equipa: str, logo_url: str):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        UPDATE equipas
        SET logo_url = %s
        WHERE LOWER(nome) = LOWER(%s)
    """, (logo_url, nome_equipa))
    alteradas = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()

    if alteradas == 0:
        return await ctx.send("⚠️ Essa equipa não existe.")

    await ctx.send(f"✅ Logo da equipa **{nome_equipa}** atualizado.")


@bot.command()
@commands.has_permissions(administrator=True)
async def apagarequipa(ctx, *, nome_equipa: str):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    alteradas = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()

    if alteradas == 0:
        return await ctx.send("⚠️ Essa equipa não existe.")

    await ctx.send(f"🗑️ A equipa **{nome_equipa}** foi apagada.")


@bot.command()
async def equipa(ctx, *, nome_equipa: str):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome, logo_url
        FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    equipa_row = cursor.fetchone()

    if not equipa_row:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Essa equipa não existe.")

    equipa_id, nome, logo_url = equipa_row

    cursor.execute("""
        SELECT user_id
        FROM equipas_membros
        WHERE equipa_id = %s
    """, (equipa_id,))
    membros = cursor.fetchall()

    cursor.close()
    conn.close()

    pontos = calcular_pontos_equipa_sync(equipa_id)
    if pontos is None:
        return await ctx.send(DB_ERROR_MSG)

    lista_membros = ""
    for (uid,) in membros:
        membro = ctx.guild.get_member(uid)
        nome_membro = membro.mention if membro else f"<@{uid}>"
        dados = calcular_pontos_jogador_sync(uid)
        total_membro = dados["total"] if dados else 0
        lista_membros += f"• {nome_membro} — **{total_membro} pts**\n"

    if not lista_membros:
        lista_membros = "Nenhum membro nesta equipa."

    embed = discord.Embed(
        title=f"🛡️ Equipa {nome}",
        description="Perfil competitivo da equipa",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    if logo_url:
        embed.set_thumbnail(url=logo_url)

    embed.add_field(name="👥 Membros", value=lista_membros[:1024], inline=False)
    embed.add_field(
        name="📊 Pontos da Equipa",
        value=(
            f"🔥 Solo Rebirth: **{pontos['solo']}**\n"
            f"👥 Team Wins: **{pontos['team']}**\n\n"
            f"🏆 Total: **{pontos['total']} pontos**"
        ),
        inline=False
    )
    embed.set_footer(text=f"{pontos['membros']} membro(s) na equipa")

    await ctx.send(embed=embed)


@bot.command()
async def rankingequipas(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome, logo_url
        FROM equipas
    """)
    equipas = cursor.fetchall()
    cursor.close()
    conn.close()

    if not equipas:
        return await ctx.send("⚠️ Ainda não existem equipas criadas.")

    ranking = []

    for equipa_id, nome, logo_url in equipas:
        pontos = calcular_pontos_equipa_sync(equipa_id)
        if pontos:
            ranking.append({
                "nome": nome,
                "logo_url": logo_url,
                "total": pontos["total"],
                "solo": pontos["solo"],
                "team": pontos["team"],
            })

    ranking.sort(key=lambda x: x["total"], reverse=True)

    if not ranking:
        return await ctx.send("⚠️ Ainda não existem equipas com pontos.")

    medalhas = [
        ("🥇 1.º Lugar", discord.Color.gold()),
        ("🥈 2.º Lugar", discord.Color.light_grey()),
        ("🥉 3.º Lugar", discord.Color.orange()),
    ]

    for i, equipa_info in enumerate(ranking[:3]):
        titulo, cor = medalhas[i]

        embed = discord.Embed(
            title=titulo,
            description=f"🛡️ **{equipa_info['nome']}**",
            color=cor,
            timestamp=discord.utils.utcnow()
        )

        if equipa_info["logo_url"]:
            embed.set_thumbnail(url=equipa_info["logo_url"])

        embed.add_field(
            name="🏆 Pontos",
            value=(
                f"**Total:** {equipa_info['total']} pontos\n"
                f"🔥 Solo Rebirth: {equipa_info['solo']}\n"
                f"👥 Team Wins: {equipa_info['team']}"
            ),
            inline=False
        )

        embed.set_footer(text="💡 Para ver mais equipas usa !listarequipas")

        await ctx.send(embed=embed)

@bot.command()
async def listarequipas(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("SELECT id, nome FROM equipas ORDER BY nome ASC")
    equipas = cursor.fetchall()
    cursor.close()
    conn.close()

    if not equipas:
        return await ctx.send("⚠️ Ainda não existem equipas criadas.")

    msg = "**🛡️ Equipas criadas:**\n"
    for _, nome in equipas:
        linha = f"• {nome}\n"
        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""
        msg += linha

    if msg:
        await ctx.send(msg)


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
    cursor.execute("UPDATE pontos SET pontos = %s, ultima_atividade = NOW() WHERE user_id = %s", (novo_total, membro.id))

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

    await ctx.send(f"👥 {membro.display_name} agora tem **{total} vitórias no TeamWin**")


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

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} vitórias no TeamWin**")


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

    await ctx.send(f"👥 {membro.display_name} tem **{total} vitórias no TeamWin!**")


@bot.command()
async def rankingteam(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_team ORDER BY pontos DESC")
    dados = cursor.fetchall()

    msg = "**🏆 Ranking TeamWin:**\n"

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

    cursor.execute("SELECT pontos, ultima_atividade FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0
    ultima_atividade = r[1] if r else None

    cursor.execute("SELECT user_id FROM pontos ORDER BY pontos DESC")
    ranking_dados = [uid for (uid,) in cursor.fetchall()]
    rank_pontos = ranking_dados.index(membro.id) + 1 if membro.id in ranking_dados else "N/A"

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_solo = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_solo ORDER BY pontos DESC")
    ranking_dados_solo = [uid for (uid,) in cursor.fetchall()]
    rank_solo = ranking_dados_solo.index(membro.id) + 1 if membro.id in ranking_dados_solo else "N/A"

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_team = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_team ORDER BY pontos DESC")
    ranking_dados_team = [uid for (uid,) in cursor.fetchall()]
    rank_team = ranking_dados_team.index(membro.id) + 1 if membro.id in ranking_dados_team else "N/A"

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
        name="👥 TEAM WIN",
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

    autor_inscricao_mention = f"<@{user_id}>"

    if canal:
        try:
            msg = await canal.fetch_message(payload.message_id)
            embed = await criar_embed_inscricao_paga(guild, canal.name, nome_jogador, autor_inscricao_mention)
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        await atualizar_embed_estado(canal)

    if user:
        try:
            await user.send(f"✅ A tua inscrição foi finalizada na thread **{canal.name if canal else 'do jogo'}**.")
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
                        "Se a inscrição não for finalizada a tempo, será cancelada."
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
            guild = canal.guild if canal else None
            autor_inscricao_mention = f"<@{user_id}>"

            if canal:
                try:
                    msg = await canal.fetch_message(message_id)
                    embed = await criar_embed_inscricao_expirada(guild, canal.name, nome_jogador, autor_inscricao_mention)
                    await msg.edit(embed=embed)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

                await atualizar_embed_estado(canal)

            if user:
                try:
                    await user.send(f"❌ A tua inscrição expirou porque passaram {HORAS_PAGAMENTO} horas sem ser finalizada.")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    cursor.close()
    conn.close()




# =========================
# 🎖️ MILSIM — OPERAÇÃO DUALITY
# =========================

COMANDO_CHANNEL_ID = 1504600088289214474
AZUL_CHANNEL_ID = 1504600244015599746
VERMELHO_CHANNEL_ID = 1504600284935094404
LOGS_CHANNEL_ID = 1504600337544122448
GM_CHANNEL_ID = 1504600378988171495

AZUL_ROLE_ID = 1504599233137868961
VERMELHO_ROLE_ID = 1504599117488455832
GM_ROLE_ID = 1504602388496121928

RESPAWN_INTERVAL_SECONDS = 300  # 5 minutos
RESPAWN_OPEN_SECONDS = 5        # janela verde de respawn
TIMEOUT_REFILL_SECONDS = 300   # 5 minutos para reagrupamento/refill quando missão expira
SATCOM_HACK_SECONDS = 600      # 10 minutos de hack SATCOM
SATCOM_TEAM_SIZE = 5
SATCOM_SECONDARY_DELAY = 180  # 3 minutos até ativar missão secundária
SATCOM_SECONDARY_SECONDS = 900  # 15 minutos para missão secundária do acampamento

# ---------- CORES DOS EMBEDS MILSIM ----------
MILSIM_COLOR_MAIN_MISSION = discord.Color.from_rgb(255, 255, 255)  # branco
MILSIM_COLOR_SUCCESS = discord.Color.green()
MILSIM_COLOR_FAILED = discord.Color.red()
MILSIM_COLOR_TIMER = discord.Color.orange()
MILSIM_COLOR_TIMEOUT = discord.Color.yellow()

# ---------- CÓDIGOS DE OPERADOR / CAPTURA ----------
AZUL_OPERATOR_CODES = {
    "AZ-117": "GHOST-1",
    "AZ-204": "GHOST-2",
    "AZ-318": "GHOST-3",
    "AZ-442": "GHOST-4",
    "AZ-509": "GHOST-5",
    "AZ-661": "GHOST-6",
    "AZ-734": "GHOST-7",
    "AZ-845": "GHOST-8",
    "AZ-918": "GHOST-9",
    "AZ-990": "GHOST-10",
}

VERMELHO_OPERATOR_CODES = {
    "VR-103": "VIPER-1",
    "VR-216": "VIPER-2",
    "VR-344": "VIPER-3",
    "VR-451": "VIPER-4",
    "VR-578": "VIPER-5",
    "VR-602": "VIPER-6",
    "VR-745": "VIPER-7",
    "VR-811": "VIPER-8",
    "VR-923": "VIPER-9",
    "VR-997": "VIPER-10",
}

ALL_OPERATOR_CODES = {
    **AZUL_OPERATOR_CODES,
    **VERMELHO_OPERATOR_CODES,
}



MISSION_TIME_LIMITS = {
    "mission_1": 1800,
    "mission_2": 2400,
    "mission_3": 2100,
    "mission_4": 1800,
    "final": 1500
}

def mission4_all_objectives_finished():
    satcom = milsim_state.get("satcom", {})

    hack_done = (
        satcom.get("hack_completed")
        or satcom.get("hack_cancelled")
        or not satcom.get("hack_active")
    )

    secondary_started = (
        satcom.get("secondary_active")
        or satcom.get("secondary_completed")
        or satcom.get("secondary_winner") is not None
    )

    secondary_done = (
        satcom.get("secondary_completed")
        or satcom.get("secondary_winner") is not None
    )

    # Se a secundária ainda nem começou, a Missão 4 ainda não pode avançar para a final.
    return bool(hack_done and secondary_started and secondary_done)


async def warn_mission4_not_finished(ctx=None):
    msg = (
        "⚠️ A Missão Final ainda não pode começar.\n"
        "A Missão 4 só termina quando a SATCOM principal e a missão secundária estiverem ambas encerradas."
    )

    if ctx:
        await ctx.send(msg, delete_after=12)

    await milsim_log("⚠️ Tentativa de avançar para Missão Final bloqueada: Missão 4 ainda não terminou por completo.")


def respawn_allowed_for_mission(mission_name: str):
    # Missão 4/SATCOM e missão secundária da Missão 4 são morte súbita: sem respawn wave.
    if mission_name == "mission_4":
        return False
    return True


def format_time_remaining(team: str):
    end_time = milsim_state.get("mission_end_times", {}).get(team)

    if not end_time:
        return "Sem timer ativo"

    now = datetime.now(timezone.utc)
    remaining = end_time - now

    if remaining.total_seconds() <= 0:
        return "00:00 — tempo esgotado"

    total_seconds = int(remaining.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60

    return f"{minutes:02d}:{seconds:02d}"


def build_operation_status_embed():
    embed = tactical_embed(
        "📡 PAINEL OPERACIONAL — DUALITY",
        "Estado em tempo real da operação.",
        discord.Color.dark_gold(),
        [
            {"name": "Estado", "value": "Ativa" if milsim_state["active"] else "Inativa", "inline": True},
            {"name": "🔵 Score Azul", "value": str(milsim_state["scores"]["azul"]), "inline": True},
            {"name": "🔴 Score Vermelho", "value": str(milsim_state["scores"]["vermelho"]), "inline": True},
        ]
    )

    for team in ["azul", "vermelho"]:
        st = milsim_state["teams"][team]
        emoji = "🔵" if team == "azul" else "🔴"
        embed.add_field(
            name=f"{emoji} {team.upper()}",
            value=(
                f"Missão: `{st['current']}`\n"
                f"Fase: `{st['phase']}`\n"
                f"Reagrupado: `{st['regrouped']}`\n"
                f"Tempo restante: **{format_time_remaining(team)}**"
            ),
            inline=False
        )

    return embed




def _remove_mission_status_fields(embed: discord.Embed):
    status_field_names = {
        "📌 Estado da Missão",
        "📌 NOTA OPERACIONAL",
        "⏱️ Tempo Restante",
        "⏱️ Novas Ordens em",
        "⏱️ NOVAS ORDENS EM",
        "⏱️ Descanso",
        "🛌 Descanso Operacional",
        "🏆 Score",
        "📍 Motivo",
    }

    kept_fields = []
    for field in embed.fields:
        if field.name not in status_field_names:
            kept_fields.append({
                "name": field.name,
                "value": field.value,
                "inline": field.inline
            })

    embed.clear_fields()

    for field in kept_fields:
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field["inline"]
        )


    kept_fields = []
    for field in embed.fields:
        if field.name not in status_field_names:
            kept_fields.append({
                "name": field.name,
                "value": field.value,
                "inline": field.inline
            })

    embed.clear_fields()

    for field in kept_fields:
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field["inline"]
        )


    kept_fields = []
    for field in embed.fields:
        if field.name not in status_field_names:
            kept_fields.append({
                "name": field.name,
                "value": field.value,
                "inline": field.inline
            })

    embed.clear_fields()

    for field in kept_fields:
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field["inline"]
        )



    note = get_operational_note_for_embed(embed)
    if not note:
        return embed

    embed.add_field(
        name="📌 NOTA OPERACIONAL",
        value=note,
        inline=False
    )
    return embed





    kept_fields = []
    replaced = False

    for field in embed.fields:
        field_name = field.name or ""

        if field_name in ("🎯 OBJETIVOS", "🎯 OBJETIVO", "📌 OBJETIVO DA MISSÃO"):
            kept_fields.append({
                "name": "📌 OBJETIVO DA MISSÃO",
                "value": objective,
                "inline": False
            })
            replaced = True
        else:
            kept_fields.append({
                "name": field.name,
                "value": field.value,
                "inline": field.inline
            })

    if not replaced:
        kept_fields.append({
            "name": "📌 OBJETIVO DA MISSÃO",
            "value": objective,
            "inline": False
        })

    embed.clear_fields()

    for field in kept_fields:
        embed.add_field(
            name=field["name"],
            value=field["value"],
            inline=field["inline"]
        )

    return embed




    kept_fields = []
    replaced = False

    for field in embed.fields:
        field_name = field.name or ""
        if field_name in ("🎯 OBJETIVOS", "🎯 OBJETIVO", "📌 OBJETIVO DA MISSÃO"):
            kept_fields.append({"name": "📌 OBJETIVO DA MISSÃO", "value": objective, "inline": False})
            replaced = True
        else:
            kept_fields.append({"name": field.name, "value": field.value, "inline": field.inline})

    if not replaced:
        kept_fields.append({"name": "📌 OBJETIVO DA MISSÃO", "value": objective, "inline": False})

    embed.clear_fields()

    for field in kept_fields:
        embed.add_field(name=field["name"], value=field["value"], inline=field["inline"])

    return embed



def get_clear_objective_for_embed(embed: discord.Embed):
    title = (embed.title or "").upper()
    desc = (embed.description or "").upper()
    combined = title + "\n" + desc

    if "MISSÃO 01" in combined and "SECURE DRIVES" in combined and "TASK FORCE AZUL" in combined:
        return '▸ Recuperar a intel operacional e garantir a sua segurança na caixa segura\n▸ Transportar a caixa até ao HQ para análise\n▸ Abrir os discos e identificar o código de validação correto\n▸ Validar o código para concluir a operação'

    if "MISSÃO 01" in combined and ("INTERCEPT PROTOCOL" in combined or "SECURE DRIVES" in combined) and "TASK FORCE VERMELHA" in combined:
        return '▸ Localizar a Task Force Azul e intercetar a caixa segura antes da extração\n▸ Capturar a caixa e transportá-la até ao HQ Vermelho\n▸ Procurar o código de validação gravado na própria caixa\n▸ Validar o código para concluir a operação'

    if ("DATA TRANSFER" in combined or "SIGNAL KEY" in combined) and "TASK FORCE AZUL" in combined:
        return '▸ Transportar a caixa segura até ao bunker operacional\n▸ Localizar o ponto exato de transmissão de dados\n▸ Validar o código de ativação localizado junto ao ponto de transmissão\n▸ Iniciar a transmissão e manter controlo da posição'

    if ("SIGNAL BREAK" in combined or "SIGNAL KEY" in combined or "DATA TRANSFER" in combined) and "TASK FORCE VERMELHA" in combined:
        return '▸ Localizar a Task Force Azul e infiltrar o bunker operacional\n▸ Alcançar o ponto onde se encontra a caixa segura\n▸ Validar o código de sabotagem localizado junto da caixa\n▸ Cortar/interromper o envio de dados antes da conclusão da transmissão'

    if "RECOVER FRAGMENTS" in combined and "TASK FORCE AZUL" in combined:
        return '▸ Localizar os CDs com fragmentos da intel perdida\n▸ Recolher as partes necessárias para reconstruir o código de ativação\n▸ Regressar ao HQ para decifrar a informação recuperada\n▸ Validar o código para concluir a missão'

    if "DENY RECOVERY" in combined and "TASK FORCE VERMELHA" in combined:
        return '▸ Localizar as zonas onde estão os CDs com fragmentos da intel perdida\n▸ Intercetar a Task Force Azul antes da recuperação das provas\n▸ Defender os locais e impedir o registo dos CDs\n▸ A Task Force Vermelha não tem autorização para mover ou recuperar os CDs'

    if "CONVOY RUN" in combined and "TASK FORCE VERMELHA" in combined:
        return '▸ Transportar a caixa pelo percurso operacional indicado no mapa\n▸ Alcançar cada ponto designado com a caixa sob controlo\n▸ Validar os códigos nos checkpoints para desbloquear o próximo ponto\n▸ Garantir que a carga chega ao depósito final'

    if "INTERCEPT CONVOY" in combined and "TASK FORCE AZUL" in combined:
        return '▸ Intercetar o convoy Vermelho durante o transporte da caixa\n▸ Capturar a caixa e extrair até ao HQ Azul\n▸ Procurar o código de validação gravado na própria caixa\n▸ Validar o código para concluir a missão'

    if "SATCOM BREACH" in combined and "TASK FORCE VERMELHA" in combined:
        return '▸ Localizar o terminal SATCOM ativo no setor CQB\n▸ Validar o código de ativação localizado junto ao terminal\n▸ Iniciar o hack às comunicações inimigas\n▸ Manter controlo da posição e intercetar a Task Force Azul até conclusão do hack (10 min)'

    if ("SATCOM COMPROMETIDO" in combined or "SATCOM EM RISCO" in combined or "INTERFERÊNCIAS DETETADAS" in combined) and "TASK FORCE AZUL" in combined:
        return '▸ Localizar o terminal SATCOM ativo no setor CQB\n▸ Intercetar a Task Force Vermelha antes da conclusão do hack\n▸ Validar o código localizado junto ao SATCOM\n▸ Sabotar/interromper o hack para preservar as comunicações Azul'

    if "DEFESA DO ACAMPAMENTO" in combined and "TASK FORCE VERMELHA" in combined:
        return '▸ Localizar a caixa de suprimentos no acampamento\n▸ Manter controlo do perímetro e impedir qualquer extração inimiga\n▸ Eliminar ameaças dentro da área operacional\n▸ A caixa não pode ser movida e a equipa não deve abandonar o perímetro do acampamento'

    if "EMBOSCADA AO ACAMPAMENTO" in combined and "TASK FORCE AZUL" in combined:
        return '▸ Localizar o acampamento Vermelho\n▸ Eliminar a resistência inimiga na zona\n▸ Capturar a caixa de suprimentos e extrair até ao HQ Azul\n▸ Procurar o código gravado na própria caixa e validar para concluir a missão'

    if "TOTAL DOMINATION" in combined and "TASK FORCE VERMELHA" in combined:
        return '▸ Localizar os dispositivos de controlo de zona (timers)\n▸ Validar a equipa para iniciar contagem a favor da Task Force Vermelha\n▸ Manter domínio das zonas até garantir superioridade operacional'

    if "TOTAL DOMINATION" in combined and "TASK FORCE AZUL" in combined:
        return '▸ Localizar os dispositivos de controlo de zona (timers)\n▸ Validar a equipa para iniciar contagem a favor da Task Force Azul\n▸ Manter domínio das zonas até garantir superioridade operacional'

    return None


def apply_clear_objective_to_embed(embed: discord.Embed):
    objective = get_clear_objective_for_embed(embed)
    if not objective:
        return embed

    kept_fields = []
    replaced = False

    for field in embed.fields:
        field_name = field.name or ""
        if field_name in ("🎯 OBJETIVOS", "🎯 OBJETIVO", "📌 OBJETIVO DA MISSÃO"):
            kept_fields.append({"name": "📌 OBJETIVO DA MISSÃO", "value": objective, "inline": False})
            replaced = True
        else:
            kept_fields.append({"name": field.name, "value": field.value, "inline": field.inline})

    if not replaced:
        kept_fields.append({"name": "📌 OBJETIVO DA MISSÃO", "value": objective, "inline": False})

    embed.clear_fields()

    for field in kept_fields:
        embed.add_field(name=field["name"], value=field["value"], inline=field["inline"])

    return embed


def build_mission_embed_with_status(team: str, embed: discord.Embed, estado: str = None):
    # Limpa campos automáticos antigos.
    # O "Estado da Missão" foi removido dos embeds porque estava a ficar desatualizado/bugado.
    final_embed = embed.copy()
    _remove_mission_status_fields(final_embed)
    final_embed = apply_clear_objective_to_embed(final_embed)
    return aplicar_cor_milsim(final_embed)

def build_team_status_embed(team: str):
    # Mantido por compatibilidade com comandos/funções antigas.
    emoji = "🔵" if team == "azul" else "🔴"
    color = discord.Color.blue() if team == "azul" else discord.Color.red()

    return tactical_embed(
        f"{emoji} STATUS OPERACIONAL — {team.upper()}",
        "Painel automático da operação.",
        color,
        [
            {"name": "⏱️ Novas Ordens em" if st.get("phase") in ("regroup", "rest", "ready") else "⏱️ Tempo Restante", "value": f"**{format_time_remaining(team)}**", "inline": True},
            {"name": "🏆 Score", "value": f"**{milsim_state['scores'][team]} pts**", "inline": True},
        ],
        footer="COMANDO CENTRAL • STATUS TÁTICO"
    )


async def create_team_status_panel(team: str):
    # Os painéis separados foram removidos.
    # O tempo e o score ficam agora dentro do embed principal da missão.
    return


async def update_team_status_panel(team: str):
    await update_team_mission_embed(team)


async def update_all_team_status_panels():
    await update_team_mission_embed("azul")
    await update_team_mission_embed("vermelho")


async def delete_team_status_panel(team: str):
    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("team_status_panel_message_ids", {}).get(team)

    if not channel or not message_id:
        return

    try:
        msg = await channel.fetch_message(message_id)
        await msg.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    milsim_state["team_status_panel_message_ids"][team] = None


async def purge_team_status_panels(team: str):
    channel = milsim_channel_for_team(team)
    tracked_message_id = milsim_state.get("team_status_panel_message_ids", {}).get(team)

    if not channel:
        return

    if tracked_message_id:
        try:
            msg = await channel.fetch_message(tracked_message_id)
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    # Remove painéis antigos duplicados, caso existam no fim do canal.
    try:
        async for msg in channel.history(limit=25):
            if msg.author == bot.user and msg.embeds:
                title = msg.embeds[0].title or ""
                if "STATUS OPERACIONAL" in title:
                    try:
                        await msg.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
    except (discord.Forbidden, discord.HTTPException):
        pass

    milsim_state["team_status_panel_message_ids"][team] = None


async def refresh_team_status_panel(team: str):
    await purge_team_status_panels(team)


def build_inactive_mission_embed(team: str, embed: discord.Embed, reason: str = "Missão encerrada"):
    # Arquiva a mensagem sem adicionar "Estado da Missão" nem "Motivo".
    final_embed = embed.copy()
    _remove_mission_status_fields(final_embed)
    final_embed.set_footer(text="COMANDO CENTRAL • MISSÃO ARQUIVADA")
    return final_embed


async def archive_team_mission_embed(team: str, reason: str = "Missão encerrada"):
    channel = milsim_channel_for_team(team)

    current_id = milsim_state.get("mission_message_ids", {}).get(team)
    previous_id = milsim_state.get("previous_mission_message_ids", {}).get(team)

    candidate_ids = []
    if previous_id and previous_id != current_id:
        candidate_ids.append(previous_id)
    if current_id:
        candidate_ids.append(current_id)

    if not channel or not candidate_ids:
        return

    for message_id in candidate_ids:
        try:
            msg = await channel.fetch_message(message_id)
            if msg.embeds:
                inactive_embed = build_inactive_mission_embed(team, msg.embeds[0], reason)
                await msg.edit(embed=inactive_embed)

                if message_id == previous_id:
                    milsim_state.setdefault("previous_mission_message_ids", {})[team] = None
                return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            continue


async def archive_all_mission_embeds(reason: str = "Missão encerrada"):
    await archive_team_mission_embed("azul", reason)
    await archive_team_mission_embed("vermelho", reason)


def build_inactive_mission_embed(team: str, embed: discord.Embed, reason: str = "Missão encerrada"):
    # Arquiva a mensagem sem adicionar "Estado da Missão" nem "Motivo".
    final_embed = embed.copy()
    _remove_mission_status_fields(final_embed)
    final_embed.set_footer(text="COMANDO CENTRAL • MISSÃO ARQUIVADA")
    return final_embed


async def deactivate_current_mission_embed(team: str, reason: str = "Missão encerrada"):
    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("mission_message_ids", {}).get(team)

    if not channel or not message_id:
        return

    try:
        msg = await channel.fetch_message(message_id)
        if msg.embeds:
            await msg.edit(embed=build_inactive_mission_embed(team, msg.embeds[0], reason))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def deactivate_all_current_mission_embeds(reason: str = "Missão encerrada"):
    await deactivate_current_mission_embed("azul", reason)
    await deactivate_current_mission_embed("vermelho", reason)


def mission_display_name(mission_name: str):
    names = {
        "mission_1": "SECURE DRIVES",
        "mission_2": "SIGNAL KEY / RECOVER FRAGMENTS",
        "mission_3": "CONVOY RUN",
        "mission_4": "SATCOM BREACH",
        "final": "TOTAL DOMINATION",
    }
    return names.get(mission_name, str(mission_name).upper())


def get_timer_context(team: str):
    phase = milsim_state["teams"][team].get("phase", "mission")
    current = milsim_state["teams"][team].get("current", "unknown")

    if phase == "mission":
        title = f"⏱️ TIMER — {current.upper()}"
        label = "Tempo restante para executar"
        color = MILSIM_COLOR_TIMER
    elif phase in ("regroup", "rest"):
        title = "⏱️ TIMER — REAGRUPAMENTO / DESCANSO"
        label = "Tempo restante para novas ordens"
        color = MILSIM_COLOR_TIMER
    elif phase == "failed":
        title = "⏱️ TIMER — MISSÃO ENCERRADA"
        label = "Estado"
        color = MILSIM_COLOR_TIMER
    else:
        title = f"⏱️ TIMER — {phase.upper()}"
        label = "Tempo restante"
        color = MILSIM_COLOR_TIMER

    return title, label, color


def format_secondary_time_remaining():
    end_time = milsim_state.get("satcom", {}).get("secondary_end_time")

    if not end_time:
        return "Sem timer ativo"

    now = datetime.now(timezone.utc)
    remaining = end_time - now

    if remaining.total_seconds() <= 0:
        return "00:00 — tempo esgotado"

    total_seconds = int(remaining.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60

    return f"{minutes:02d}:{seconds:02d}"


def build_secondary_timer_embed(team: str):
    color = discord.Color.blue() if team == "azul" else discord.Color.red()

    title = "⏱️ TIMER — MISSÃO SECUNDÁRIA"
    label = "Tempo restante da operação no acampamento"

    return tactical_embed(
        title,
        "Painel separado de tempo da missão secundária.",
        color,
        [
            {"name": label, "value": f"**{format_secondary_time_remaining()}**", "inline": False}
        ],
        footer="COMANDO CENTRAL • TIMER SECUNDÁRIO"
    )


async def cleanup_secondary_timer_panels(team: str, limit: int = 100):
    channel = milsim_channel_for_team(team)
    if not channel:
        return

    try:
        async for msg in channel.history(limit=limit):
            if msg.author == bot.user and msg.embeds:
                embed = msg.embeds[0]
                title = embed.title or ""
                footer = embed.footer.text if embed.footer else ""
                description = embed.description or ""

                is_timer = (
                    title.startswith("⏱️ TIMER — MISSÃO SECUNDÁRIA")
                    or "TIMER SECUNDÁRIO" in footer
                    or "Painel separado de tempo da missão secundária" in description
                )

                if is_timer:
                    try:
                        await msg.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
    except (discord.Forbidden, discord.HTTPException):
        pass

    milsim_state.setdefault("secondary_timer_panel_message_ids", {})[team] = None


async def create_secondary_timer_panel(team: str):
    await cleanup_secondary_timer_panels(team)

    channel = milsim_channel_for_team(team)
    if not channel:
        return None

    msg = await channel.send(embed=build_secondary_timer_embed(team))
    milsim_state.setdefault("secondary_timer_panel_message_ids", {})[team] = msg.id
    return msg


async def update_secondary_timer_panel(team: str):
    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("secondary_timer_panel_message_ids", {}).get(team)

    if not channel or not message_id:
        return

    try:
        msg = await channel.fetch_message(message_id)
        await msg.edit(embed=build_secondary_timer_embed(team))
    except discord.NotFound:
        milsim_state.setdefault("secondary_timer_panel_message_ids", {})[team] = None
    except (discord.Forbidden, discord.HTTPException):
        pass


async def update_all_secondary_timer_panels():
    satcom = milsim_state.get("satcom", {})
    if not satcom.get("secondary_active"):
        return

    await update_secondary_timer_panel("azul")
    await update_secondary_timer_panel("vermelho")


async def delete_all_secondary_timer_panels():
    await cleanup_secondary_timer_panels("azul")
    await cleanup_secondary_timer_panels("vermelho")


def build_team_timer_embed(team: str):
    title, label, color = get_timer_context(team)

    return tactical_embed(
        title,
        "Painel separado de tempo operacional.",
        color,
        [
            {"name": label, "value": f"**{format_time_remaining(team)}**", "inline": False}
        ],
        footer="COMANDO CENTRAL • TIMER OPERACIONAL"
    )


async def delete_team_timer_panel(team: str):
    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("team_timer_panel_message_ids", {}).get(team)

    if not channel or not message_id:
        return

    try:
        msg = await channel.fetch_message(message_id)
        await msg.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    milsim_state.setdefault("team_timer_panel_message_ids", {})[team] = None


async def cleanup_team_timer_panels(team: str, limit: int = 100):
    channel = milsim_channel_for_team(team)
    if not channel:
        return

    try:
        async for msg in channel.history(limit=limit):
            if msg.author == bot.user and msg.embeds:
                embed = msg.embeds[0]
                title = embed.title or ""
                footer = embed.footer.text if embed.footer else ""
                description = embed.description or ""

                is_timer = (
                    title.startswith("⏱️ TIMER")
                    or "TIMER OPERACIONAL" in footer
                    or "Painel separado de tempo operacional" in description
                )

                if is_timer:
                    try:
                        await msg.delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
    except (discord.Forbidden, discord.HTTPException):
        pass

    milsim_state.setdefault("team_timer_panel_message_ids", {})[team] = None


async def create_team_timer_panel(team: str):
    await cleanup_team_timer_panels(team)

    channel = milsim_channel_for_team(team)
    if not channel:
        return None

    msg = await channel.send(embed=build_team_timer_embed(team))
    milsim_state.setdefault("team_timer_panel_message_ids", {})[team] = msg.id
    return msg


async def update_team_timer_panel(team: str):
    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("team_timer_panel_message_ids", {}).get(team)

    if not channel or not message_id:
        return

    try:
        msg = await channel.fetch_message(message_id)
        await msg.edit(embed=build_team_timer_embed(team))
    except discord.NotFound:
        milsim_state.setdefault("team_timer_panel_message_ids", {})[team] = None
    except (discord.Forbidden, discord.HTTPException):
        pass


async def update_all_team_timer_panels():
    await update_team_timer_panel("azul")
    await update_team_timer_panel("vermelho")


async def force_archive_current_mission_embed(team: str, reason: str = "Nova fase operacional iniciada."):
    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("mission_message_ids", {}).get(team)

    if not channel or not message_id:
        return

    try:
        msg = await channel.fetch_message(message_id)
        if not msg.embeds:
            return

        old_embed = msg.embeds[0].copy()
        _remove_mission_status_fields(old_embed)

        # Ao arquivar embeds antigos, removemos campos operacionais temporários.
        # O Estado da Missão deve aparecer apenas nos embeds das missões principais.
        old_embed.set_footer(text="COMANDO CENTRAL • MISSÃO ARQUIVADA")

        await msg.edit(embed=old_embed)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def force_archive_all_current_mission_embeds(reason: str = "Nova fase operacional iniciada."):
    await force_archive_current_mission_embed("azul", reason)
    await force_archive_current_mission_embed("vermelho", reason)


def remove_estado_missao_field(embed: discord.Embed):
    _remove_mission_status_fields(embed)
    return embed


async def send_team_embed_plain(team: str, embed: discord.Embed):
    await force_archive_current_mission_embed(team, "Nova transmissão operacional emitida.")
    await purge_team_status_panels(team)
    await cleanup_team_timer_panels(team)

    channel = milsim_channel_for_team(team)
    if not channel:
        return None

    clean_embed = remove_estado_missao_field(embed.copy())
    clean_embed = aplicar_cor_milsim(clean_embed)

    msg = await channel.send(embed=clean_embed)
    milsim_state.setdefault("mission_message_ids", {})[team] = msg.id

    await finish_team_channel_after_new_milsim_embed(team)
    await create_team_status_panel(team)
    return msg


async def send_team_embed_with_status_last(team: str, embed: discord.Embed, estado: str = None):
    # Arquivar visualmente o embed operacional anterior antes de enviar nova missão/descanso/reagrupamento.
    await force_archive_current_mission_embed(team, "Nova transmissão operacional emitida.")

    await purge_team_status_panels(team)
    await cleanup_team_timer_panels(team)

    channel = milsim_channel_for_team(team)
    if not channel:
        return None

    msg = await channel.send(embed=build_mission_embed_with_status(team, embed, estado))
    milsim_state.setdefault("mission_message_ids", {})[team] = msg.id

    await finish_team_channel_after_new_milsim_embed(team)
    await create_team_status_panel(team)
    return msg


async def update_team_mission_embed(team: str, estado: str = None):
    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("mission_message_ids", {}).get(team)

    if not channel or not message_id:
        return

    try:
        msg = await channel.fetch_message(message_id)
        if not msg.embeds:
            return

        base_embed = msg.embeds[0]
        await msg.edit(embed=build_mission_embed_with_status(team, base_embed, estado))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    await update_team_timer_panel(team)


def calculate_remaining_mission_seconds(team: str) -> int:
    end_time = milsim_state.get("mission_end_times", {}).get(team)
    if not end_time:
        return 0
    remaining = end_time - datetime.now(timezone.utc)
    return max(0, int(remaining.total_seconds()))


async def set_team_to_regroup_after_objective(team: str):
    team_state = milsim_state["teams"][team]
    remaining_seconds = calculate_remaining_mission_seconds(team)

    team_state["phase"] = "regroup"
    team_state["regrouped"] = False
    milsim_state["rest_seconds"][team] = remaining_seconds
    milsim_state["rest_until"][team] = None
    milsim_state["rest_ready"][team] = False
    milsim_state["rest_warned"][team] = False


async def advance_both_teams_to_next_mission(old_mission: str):
    if old_mission not in NEXT_MISSIONS:
        return

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "mission"
        milsim_state["teams"][t]["regrouped"] = False
        milsim_state["rest_seconds"][t] = 0
        milsim_state["rest_until"][t] = None
        milsim_state["rest_ready"][t] = False
        milsim_state["rest_warned"][t] = False

        if old_mission == "mission_4":
            milsim_state["teams"][t]["current"] = "final"
        else:
            next_number = int(old_mission.split("_")[1]) + 1
            milsim_state["teams"][t]["current"] = f"mission_{next_number}"

        if milsim_state["teams"][t]["current"] == "mission_3":
            milsim_state["mission3_route"]["vermelho_step"] = 0

        if milsim_state["teams"][t]["current"] == "mission_4":
            await stop_respawn_cycle()
            # Missão 4 sem respawn wave.

        milsim_state["teams"][t]["completed_codes"] = []
        milsim_state["mission_end_times"][t] = datetime.now(timezone.utc) + timedelta(
            seconds=MISSION_TIME_LIMITS[milsim_state["teams"][t]["current"]]
        )

        await send_team_embed_with_status_last(t, NEXT_MISSIONS[old_mission][t]())

    for t in ["azul", "vermelho"]:
        asyncio.create_task(mission_timer(t, milsim_state["teams"][t]["current"]))

    await milsim_log("📡 Nova fase operacional transmitida automaticamente às duas equipas.")
    await update_status_panel()


async def try_advance_after_rest():
    if not milsim_state["active"]:
        return

    if not (milsim_state["rest_ready"].get("azul") and milsim_state["rest_ready"].get("vermelho")):
        return

    old_mission = milsim_state["teams"]["azul"]["current"]
    if milsim_state["teams"]["vermelho"]["current"] != old_mission:
        return

    if old_mission not in NEXT_MISSIONS:
        return

    if old_mission == "mission_4" and not mission4_all_objectives_finished():
        await warn_mission4_not_finished()
        return

    await advance_both_teams_to_next_mission(old_mission)


async def rest_countdown(team: str, mission_name: str, rest_seconds: int):
    try:
        if rest_seconds > 60:
            await asyncio.sleep(rest_seconds - 60)

            if not milsim_state["active"]:
                return
            if milsim_state["teams"][team]["current"] != mission_name:
                return
            if milsim_state["teams"][team]["phase"] != "rest":
                return

            milsim_state["rest_warned"][team] = True
            await milsim_send_to_team(
                team,
                embed=tactical_embed(
                    "📡 NOVAS ORDENS EM 1 MINUTO",
                    "Descanso operacional quase concluído.\n\nPreparem-se para receber nova janela de missão.",
                    discord.Color.orange()
                )
            )
            await update_team_mission_embed(team, "🛌 Descanso operacional — novas ordens em 1 minuto")
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(max(rest_seconds, 0))

        if not milsim_state["active"]:
            return
        if milsim_state["teams"][team]["current"] != mission_name:
            return
        if milsim_state["teams"][team]["phase"] != "rest":
            return

        milsim_state["teams"][team]["phase"] = "ready"
        milsim_state["rest_ready"][team] = True
        milsim_state["rest_until"][team] = None

        await milsim_send_to_team(
            team,
            embed=tactical_embed(
                "✅ DESCANSO OPERACIONAL CONCLUÍDO",
                "Unidade pronta. Aguardando sincronização operacional para novas ordens.",
                discord.Color.green()
            )
        )
        await update_team_mission_embed(team, "✅ Unidade pronta / Aguardando ambas as equipas")
        await milsim_log(f"✅ Descanso operacional concluído para **{team.upper()}**.")
        await update_status_panel()
        await try_advance_after_rest()

    except asyncio.CancelledError:
        return


async def start_team_rest_after_regroup(team: str):
    team_state = milsim_state["teams"][team]
    mission_name = team_state["current"]
    rest_seconds = 0 if team_state["phase"] == "failed" else milsim_state.get("rest_seconds", {}).get(team, 0)
    rest_seconds = max(0, int(rest_seconds or 0))

    old_task = milsim_state.get("rest_tasks", {}).get(team)
    if old_task and not old_task.done():
        old_task.cancel()

    team_state["regrouped"] = True

    if rest_seconds <= 0:
        team_state["phase"] = "ready"
        milsim_state["rest_ready"][team] = True
        milsim_state["rest_until"][team] = None

        await milsim_send_to_team(
            team,
            embed=tactical_embed(
                "✅ BASE ALCANÇADA",
                "Unidade reagrupada no HQ. Sem descanso operacional restante.\n\nAguardando sincronização para novas ordens.",
                discord.Color.green()
            )
        )
        await update_team_mission_embed(team, "✅ Unidade pronta / Aguardando ambas as equipas")
        await milsim_log(f"✅ **{team.upper()}** chegou à base e está pronta.")
        await update_status_panel()
        await try_advance_after_rest()
        return

    rest_until = datetime.now(timezone.utc) + timedelta(seconds=rest_seconds)
    team_state["phase"] = "rest"
    milsim_state["rest_until"][team] = rest_until
    milsim_state["rest_ready"][team] = False
    milsim_state["rest_warned"][team] = False

    await milsim_send_to_team(
        team,
        embed=tactical_embed(
            "🛌 BASE ALCANÇADA — DESCANSO OPERACIONAL",
            "Unidade reagrupada no HQ. O tempo restante da missão foi convertido em descanso operacional.",
            discord.Color.dark_gold(),
            [
                {"name": "⏱️ Descanso", "value": f"Novas ordens <t:{int(rest_until.timestamp())}:R>", "inline": False},
                {"name": "📡 Aviso", "value": "Quando faltar 1 minuto, o Comando Central envia alerta automático.", "inline": False}
            ]
        )
    )
    await update_team_mission_embed(team, "🛌 Descanso operacional")
    await milsim_log(f"🛌 **{team.upper()}** iniciou descanso operacional por {rest_seconds} segundo(s).")
    await update_status_panel()

    task = asyncio.create_task(rest_countdown(team, mission_name, rest_seconds))
    milsim_state["rest_tasks"][team] = task


async def mark_team_mission_failed(team: str, mission_name: str):
    team_state = milsim_state["teams"][team]
    team_state["phase"] = "failed"
    team_state["regrouped"] = False
    milsim_state["rest_seconds"][team] = 0
    milsim_state["rest_until"][team] = None
    milsim_state["rest_ready"][team] = False
    milsim_state["rest_warned"][team] = False

    channel = milsim_channel_for_team(team)
    message_id = milsim_state.get("mission_message_ids", {}).get(team)

    if channel and message_id:
        try:
            msg = await channel.fetch_message(message_id)
            if msg.embeds:
                failed_embed = msg.embeds[0].copy()
                failed_embed.title = f"❌ MISSÃO FRACASSADA — {mission_name.upper()}"
                failed_embed.color = discord.Color.red()
                failed_embed.add_field(
                    name="📍 ORDEM",
                    value="Regressem imediatamente ao **COMANDO CENTRAL** e usem `!reagrupado` quando toda a unidade estiver pronta.",
                    inline=False
                )
                await msg.edit(embed=build_mission_embed_with_status(team, failed_embed, "❌ MISSÃO FRACASSADA"))
                return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    await send_team_embed_with_status_last(
        team,
        tactical_embed(
            "❌ MISSÃO FRACASSADA",
            f"O tempo operacional da **{mission_name.upper()}** expirou.\n\nO objetivo principal não foi concluído.",
            discord.Color.red(),
            [
                {"name": "📍 ORDEM", "value": "Regressem imediatamente ao **COMANDO CENTRAL**."},
                {"name": "➡️ PRÓXIMO PASSO", "value": "Usem `!reagrupado` quando toda a unidade estiver pronta."}
            ]
        ),
        "❌ MISSÃO FRACASSADA"
    )


async def update_status_panel():
    channel = milsim_get_channel(COMANDO_CHANNEL_ID)
    message_id = milsim_state.get("status_panel_message_id")

    if channel and message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=build_operation_status_embed())
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    await update_all_team_status_panels()
    await update_all_team_timer_panels()
    await update_all_secondary_timer_panels()



async def handle_mission_timeout_failsafe(mission_name: str):
    # Failsafe narrativo para impedir bloqueio da campanha quando a Missão 1 termina sem objetivo validado.
    if mission_name != "mission_1":
        return False

    if milsim_state.get("mission_branch"):
        return False

    azul_done = bool(milsim_state["teams"]["azul"].get("completed_codes"))
    vermelho_done = bool(milsim_state["teams"]["vermelho"].get("completed_codes"))

    if azul_done or vermelho_done:
        return False

    milsim_state["mission_branch"] = "compromised"

    refill_until = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_REFILL_SECONDS)

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "regroup"
        milsim_state["teams"][t]["regrouped"] = False
        milsim_state["mission_end_times"][t] = refill_until
        milsim_state["regroup_notice_sent"][t] = False

    await stop_respawn_cycle()
    await force_archive_all_current_mission_embeds("Missão encerrada sem objetivo validado.")
    await force_archive_all_current_mission_embeds("Tempo operacional esgotado.")

    for t in ["azul", "vermelho"]:
        color = discord.Color.blue() if t == "azul" else discord.Color.red()
        embed = tactical_embed(
            "⚠️ FALHA OPERACIONAL — INTEL PERDIDA",
            "Nenhuma Task Force conseguiu assegurar a caixa segura antes do encerramento da janela operacional.\n\n"
            "Durante a retirada das unidades, parte da inteligência foi destruída e fragmentada no terreno.\n\n"
            "Parte da inteligência operacional foi perdida durante a retirada das unidades.\n\n"
            "O COMANDO CENTRAL está a reorganizar a operação e novas ordens serão transmitidas após o reagrupamento.\n\n"
            "Antes da próxima transmissão, todas as unidades têm autorização para reagrupamento, refill e reorganização.",
            color,
            [
                {"name": "📌 Resultado", "value": "Nenhuma equipa concluiu o objetivo principal da Missão 1.", "inline": False},
                {"name": "📡 Nova Diretriz", "value": "A operação seguirá para **RECOVER FRAGMENTS** após o refill.", "inline": False},
                {"name": "📍 Ordem", "value": "Regressar à base, reorganizar unidade, fazer refill e aguardar novas ordens.", "inline": False},
                {"name": "⏱️ Novas Ordens em", "value": f"**{format_time_remaining(t)}**", "inline": True}
            ],
            footer="COMANDO CENTRAL • FAILSAFE OPERACIONAL"
        )

        await send_team_embed_plain(t, embed)

    await milsim_log("⚠️ Missão 1 terminou sem objetivo validado. Failsafe ativado: 5 minutos de refill antes de RECOVER FRAGMENTS.")
    await update_status_panel()

    # Não avançar já para a Missão 2.
    # O avanço acontece automaticamente quando a janela de 5 minutos terminar.
    return True



async def set_timeout_refill_regroup(mission_name: str):
    refill_until = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_REFILL_SECONDS)

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "regroup"
        milsim_state["teams"][t]["regrouped"] = False
        milsim_state["mission_end_times"][t] = refill_until
        milsim_state["regroup_notice_sent"][t] = False

    await stop_respawn_cycle()

    for t in ["azul", "vermelho"]:
        color = discord.Color.blue() if t == "azul" else discord.Color.red()
        await send_team_embed_plain(
            t,
            tactical_embed(
                "⏱️ TEMPO ESGOTADO — REAGRUPAMENTO E REFILL",
                f"A janela operacional da **{mission_name.upper()}** terminou sem objetivo validado.\n\n"
                "Nenhuma força conseguiu concluir a missão dentro do tempo. O COMANDO CENTRAL autorizou uma janela curta de reagrupamento, hidratação e reabastecimento antes da próxima transmissão.",
                color,
                [
                    {"name": "📌 Resultado", "value": "Objetivo não concluído dentro do tempo operacional.", "inline": False},
                    {"name": "📍 Ordem", "value": "Regressar ao HQ, reorganizar unidade, fazer refill e preparar nova missão.", "inline": False},
                    {"name": "⏱️ Novas Ordens em", "value": f"**{format_time_remaining(t)}**", "inline": True}
                ],
                footer="COMANDO CENTRAL • REAGRUPAMENTO E REFILL"
            )
        )

    await milsim_log(f"⏱️ `{mission_name}` terminou por tempo esgotado. Janela de 5 minutos de reagrupamento/refill ativada.")
    await update_status_panel()


async def mission_timer(team: str, mission_name: str):
    # Apenas uma task inicia o ciclo de respawn por missão.
    if team == "azul":
        if respawn_allowed_for_mission(mission_name):
            await start_respawn_cycle(mission_name)
        else:
            await stop_respawn_cycle()

    seconds = MISSION_TIME_LIMITS[mission_name]
    end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    milsim_state["mission_end_times"][team] = end_time
    milsim_state.setdefault("regroup_notice_sent", {"azul": False, "vermelho": False})
    milsim_state["regroup_notice_sent"][team] = False

    # Timer preciso baseado no relógio real.
    while milsim_state.get("active"):
        current = milsim_state["teams"][team]["current"]
        if current != mission_name:
            return

        remaining = milsim_state["mission_end_times"][team] - datetime.now(timezone.utc)

        if remaining.total_seconds() <= 0:
            break

        # Aviso automático a 2 minutos do fim da janela operacional, se a equipa já estiver em reagrupamento.
        if (
            team == "azul"
            and milsim_state["teams"][team].get("phase") == "regroup"
            and timedelta(seconds=0) < remaining <= timedelta(minutes=2)
        ):
            try:
                await broadcast_two_minute_regroup_notice()
            except Exception as e:
                await milsim_log(f"⚠️ Erro ao enviar alerta de 2 minutos: `{e}`")

        await update_status_panel()

        # Verifica no máximo de 5 em 5 segundos, e mais rápido no último segundo.
        sleep_for = min(5, max(1, int(remaining.total_seconds())))
        await asyncio.sleep(sleep_for)

    if not milsim_state.get("active"):
        return

    current = milsim_state["teams"][team]["current"]
    if current != mission_name:
        return

    team_state = milsim_state["teams"][team]

    # Se a equipa está em reagrupamento/refill, tentar avançar quando ambas estiverem nessa fase.
    if team_state.get("phase") == "regroup":
        await milsim_log(f"📡 Janela de reagrupamento terminou para **{team.upper()}**.")
        await update_status_panel()

        other = milsim_enemy(team)
        if milsim_state["teams"][other].get("phase") == "regroup":
            if milsim_state.get("timeout_resolution_active", False):
                return

            milsim_state["timeout_resolution_active"] = True
            try:
                await advance_milsim_phase(mission_name)
            finally:
                milsim_state["timeout_resolution_active"] = False
        return

    # A partir daqui, só uma das duas tasks pode resolver o fim da missão.
    if milsim_state.get("timeout_resolution_active", False):
        return

    milsim_state["timeout_resolution_active"] = True
    try:
        # Se a Missão 1 terminar sem nenhuma equipa concluir objetivo, ativar failsafe com refill.
        if await handle_mission_timeout_failsafe(mission_name):
            return

        # Tempo esgotado sem objetivo concluído: ativar 5 minutos de reagrupamento/refill.
        await set_timeout_refill_regroup(mission_name)
        return
    finally:
        milsim_state["timeout_resolution_active"] = False


MILSPEED = {
    "decryption_seconds": 600,
    "blackout_seconds": 600
}

VALIDACAO_CODIGO_TEXTO = (
    "Quando encontrarem o código físico no terreno, validem no canal da equipa com:\n"
    "`!codigo CÓDIGO-ENCONTRADO`"
)

milsim_state = {
    "active": False,
    "timeout_resolution_active": False,
    "scores": {"azul": 0, "vermelho": 0},
    "mission_end_times": {"azul": None, "vermelho": None},
    "regroup_notice_sent": {"azul": False, "vermelho": False},
    "status_panel_message_id": None,
    "team_status_panel_message_ids": {"azul": None, "vermelho": None},
    "team_timer_panel_message_ids": {"azul": None, "vermelho": None},
    "secondary_timer_panel_message_ids": {"azul": None, "vermelho": None},
    "respawn": {
        "active": False,
        "mission": None,
        "cycle_started_at": None,
        "next_respawn_at": None,
        "panel_message_id": None,
        "task": None
    },
    "mission_message_ids": {"azul": None, "vermelho": None},
    "previous_mission_message_ids": {"azul": None, "vermelho": None},
    "rest_seconds": {"azul": 0, "vermelho": 0},
    "rest_until": {"azul": None, "vermelho": None},
    "rest_ready": {"azul": False, "vermelho": False},
    "rest_warned": {"azul": False, "vermelho": False},
    "rest_tasks": {"azul": None, "vermelho": None},
    "decryption": {"active": False, "cancelled": False, "team": None, "mission": None},
    "mission3_route": {"vermelho_step": 0},
    "teams": {
        "azul": {
            "current": "mission_1",
            "phase": "mission",
            "regrouped": False,
            "completed_codes": []
        },
        "vermelho": {
            "current": "mission_1",
            "phase": "mission",
            "regrouped": False,
            "completed_codes": []
        }
    },
    "captured_players": [],
    "mission_branch": None,
    "satcom": {
        "hack_active": False,
        "hack_completed": False,
        "hack_cancelled": False,
        "hack_end_time": None,
        "hack_task": None,
        "secondary_active": False,
        "secondary_completed": False,
        "secondary_winner": None,
        "secondary_end_time": None,
        "secondary_task": None,
        "selected": {"azul": [], "vermelho": []},
        "secondary": {"azul": [], "vermelho": []}
    }
}


def aplicar_cor_milsim(embed: discord.Embed):
    """Aplica a paleta visual da Operação Duality aos embeds principais."""
    title = (embed.title or "").upper()
    footer = (embed.footer.text if embed.footer else "").upper()

    if "TIMER" in title or "TIMER OPERACIONAL" in footer:
        embed.color = MILSIM_COLOR_TIMER
    elif "TEMPO ESGOTADO" in title:
        embed.color = MILSIM_COLOR_TIMEOUT
    elif "BEM SUCEDIDA" in title or "MISSÃO CONCLUÍDA" in title or "OBJETIVO CONCLUÍDO" in title:
        embed.color = MILSIM_COLOR_SUCCESS
    elif "FRACASSADA" in title or "MISSÃO FALHADA" in title or "MISSÃO FRACASSADA" in title:
        embed.color = MILSIM_COLOR_FAILED
    elif "MISSÃO" in title:
        embed.color = MILSIM_COLOR_MAIN_MISSION

    return embed


def tactical_embed(title, description, color=discord.Color.dark_grey(), fields=None, footer="COMANDO CENTRAL • OPERAÇÃO DUALITY"):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    if fields:
        for field in fields:
            embed.add_field(
                name=field.get("name", "\u200b"),
                value=field.get("value", "\u200b"),
                inline=field.get("inline", False)
            )
    embed.set_footer(text=footer)
    return aplicar_cor_milsim(embed)


def medical_rules_standard_field():
    return {
        "name": "⚕️ REGRAS MÉDICAS",
        "value": (
            "▸ 1 médico por equipa\n"
            "▸ 2 vidas por operador\n\n"
            "▸ Operador abatido permanece **1 minuto no solo**\n"
            "▸ Operador abatido está **sempre sujeito a captura inimiga**\n"
            "▸ Após bandagem médica, regressa ao combate na última vida operacional\n"
            "▸ Segunda eliminação: permanece 1 minuto no solo e continua sujeito a captura\n"
            "▸ Caso não seja capturado, regressa à base e aguarda a próxima janela de respawn"
        ),
        "inline": False
    }


def medical_rules_satcom_field():
    return {
        "name": "⚕️ REGRAS MÉDICAS — OPERAÇÃO 5x5",
        "value": (
            "▸ 1 médico por equipa\n"
            "▸ **Sem revive** nesta missão\n"
            "▸ Apenas os 5 operadores designados participam\n"
            "▸ Sem reforços, substituições ou respawns no terreno\n\n"
            "▸ Operador abatido permanece **1 minuto no solo**\n"
            "▸ Operador abatido está **sempre sujeito a captura inimiga**\n"
            "▸ Caso não seja capturado, regressa à base e aguarda a próxima janela de respawn"
        ),
        "inline": False
    }


def medical_rules_secondary_5v5_field():
    return {
        "name": "⚕️ REGRAS MÉDICAS — MISSÃO SECUNDÁRIA",
        "value": (
            "▸ 1 médico por equipa\n"
            "▸ Sem revive nesta missão\n"
            "▸ Operadores abatidos permanecem **1 minuto no solo**\n"
            "▸ Operador abatido está **sempre sujeito a captura inimiga**\n"
            "▸ Caso não seja capturado, regressa à base e aguarda a próxima janela de respawn"
        ),
        "inline": False
    }


def medical_rules_final_field():
    return {
        "name": "⚕️ REGRAS MÉDICAS — FASE FINAL",
        "value": (
            "▸ Operador abatido permanece **1 minuto no solo**\n"
            "▸ Operador abatido está **sempre sujeito a captura inimiga**\n"
            "▸ Regras finais seguem instruções do COMANDO no terreno"
        ),
        "inline": False
    }


def apply_medical_rules_to_embed(embed: discord.Embed):
    if not embed:
        return embed

    title_upper = (embed.title or "").upper()
    desc_upper = (embed.description or "").upper()
    combined = title_upper + "\n" + desc_upper

    # Nunca aplicar regras médicas em embeds que não são briefings de missão.
    blocked_keywords = [
        "REAGRUPAMENTO OPERACIONAL",
        "ORDEM DE RETIRADA",
        "OBJETIVO CONCLUÍDO",
        "DESCANSO OPERACIONAL",
        "ALERTA OPERACIONAL",
        "NOVAS ORDENS",
        "RESSURGIMENTO",
        "PAINEL",
        "OPERADOR CAPTURADO",
        "OPERADOR COMPROMETIDO",
        "FALHA OPERACIONAL",
        "MISSÃO FRACASSADA"
    ]

    if any(keyword in combined for keyword in blocked_keywords):
        return embed

    existing = any(
        (field.name or "").startswith("⚕️ REGRAS MÉDICAS")
        for field in getattr(embed, "fields", [])
    )
    if existing:
        return embed

    is_mission_embed = (
        "MISSÃO 01" in combined
        or "MISSÃO 02" in combined
        or "MISSÃO 03" in combined
        or "MISSÃO 04" in combined
        or "MISSÃO FINAL" in combined
        or "MISSÃO SECUNDÁRIA" in combined
        or "SECURE DRIVES" in combined
        or "INTERCEPT PROTOCOL" in combined
        or "DATA TRANSFER" in combined
        or "SIGNAL BREAK" in combined
        or "RECOVER FRAGMENTS" in combined
        or "DENY RECOVERY" in combined
        or "CONVOY RUN" in combined
        or "INTERCEPT CONVOY" in combined
        or "SATCOM DOMINATION" in combined
        or "AMBUSH RAID" in combined
        or "CAMP DEFENSE" in combined
        or "TOTAL DOMINATION" in combined
    )

    if not is_mission_embed:
        return embed

    if "SATCOM DOMINATION" in combined:
        embed.add_field(**medical_rules_satcom_field())
    elif "MISSÃO SECUNDÁRIA" in combined or "AMBUSH RAID" in combined or "CAMP DEFENSE" in combined:
        embed.add_field(**medical_rules_secondary_5v5_field())
    elif "TOTAL DOMINATION" in combined or "MISSÃO FINAL" in combined:
        embed.add_field(**medical_rules_final_field())
    else:
        embed.add_field(**medical_rules_standard_field())

    return embed

    title_upper = (embed.title or "").upper()
    desc_upper = (embed.description or "").upper()
    combined = title_upper + "\n" + desc_upper

    # Nunca aplicar regras médicas em embeds de reagrupamento/retirada.
    blocked_keywords = [
        "REAGRUPAMENTO OPERACIONAL",
        "ORDEM DE RETIRADA",
        "OBJETIVO CONCLUÍDO",
        "DESCANSO OPERACIONAL"
    ]

    if any(keyword in combined for keyword in blocked_keywords):
        return embed

    existing = any(
        (field.name or "").startswith("⚕️ REGRAS MÉDICAS")
        for field in getattr(embed, "fields", [])
    )
    if existing:
        return embed

    is_mission_embed = (
        "MISSÃO" in combined
        or "MISSION" in combined
        or "TASK FORCE" in combined
        or "SECURE DRIVES" in combined
        or "INTERCEPT PROTOCOL" in combined
        or "DATA TRANSFER" in combined
        or "SIGNAL BREAK" in combined
        or "RECOVER FRAGMENTS" in combined
        or "DENY RECOVERY" in combined
        or "CONVOY RUN" in combined
        or "INTERCEPT CONVOY" in combined
        or "SATCOM" in combined
        or "AMBUSH RAID" in combined
        or "CAMP DEFENSE" in combined
        or "TOTAL DOMINATION" in combined
    )

    if "SATCOM" in combined:
        embed.add_field(**medical_rules_satcom_field())
    elif "MISSÃO SECUNDÁRIA" in combined or "AMBUSH RAID" in combined or "CAMP DEFENSE" in combined:
        embed.add_field(**medical_rules_secondary_5v5_field())
    elif "TOTAL DOMINATION" in combined or "MISSÃO FINAL" in combined:
        embed.add_field(**medical_rules_final_field())
    elif is_mission_embed:
        embed.add_field(**medical_rules_standard_field())

    return embed

    existing = any(
        (field.name or "").startswith("⚕️ REGRAS MÉDICAS")
        for field in getattr(embed, "fields", [])
    )
    if existing:
        return embed

    title_upper = (embed.title or "").upper()
    desc_upper = (embed.description or "").upper()
    combined = title_upper + "\n" + desc_upper

    is_mission_embed = (
        "MISSÃO" in combined
        or "MISSION" in combined
        or "TASK FORCE" in combined
        or "SECURE DRIVES" in combined
        or "INTERCEPT PROTOCOL" in combined
        or "DATA TRANSFER" in combined
        or "SIGNAL BREAK" in combined
        or "RECOVER FRAGMENTS" in combined
        or "DENY RECOVERY" in combined
        or "CONVOY RUN" in combined
        or "INTERCEPT CONVOY" in combined
        or "SATCOM" in combined
        or "AMBUSH RAID" in combined
        or "CAMP DEFENSE" in combined
        or "TOTAL DOMINATION" in combined
    )

    if "SATCOM" in combined:
        embed.add_field(**medical_rules_satcom_field())
    elif "MISSÃO SECUNDÁRIA" in combined or "AMBUSH RAID" in combined or "CAMP DEFENSE" in combined:
        embed.add_field(**medical_rules_secondary_5v5_field())
    elif "TOTAL DOMINATION" in combined or "MISSÃO FINAL" in combined:
        embed.add_field(**medical_rules_final_field())
    elif is_mission_embed:
        embed.add_field(**medical_rules_standard_field())

    return embed

    existing = any(
        (field.name or "").startswith("⚕️ REGRAS MÉDICAS")
        for field in getattr(embed, "fields", [])
    )
    if existing:
        return embed

    title_upper = (embed.title or "").upper()
    desc_upper = (embed.description or "").upper()
    combined = title_upper + "\n" + desc_upper

    if "SATCOM" in combined:
        embed.add_field(**medical_rules_satcom_field())
    elif "MISSÃO SECUNDÁRIA" in combined or "AMBUSH RAID" in combined or "CAMP DEFENSE" in combined:
        embed.add_field(**medical_rules_secondary_5v5_field())
    elif "TOTAL DOMINATION" in combined or "MISSÃO FINAL" in combined:
        embed.add_field(**medical_rules_final_field())
    elif "MISSÃO" in combined:
        embed.add_field(**medical_rules_standard_field())

    return embed




def select_satcom_operators():
    azul_codes = list(AZUL_OPERATOR_CODES.keys())
    vermelho_codes = list(VERMELHO_OPERATOR_CODES.keys())
    azul_satcom = random.sample(azul_codes, min(SATCOM_TEAM_SIZE, len(azul_codes)))
    vermelho_satcom = random.sample(vermelho_codes, min(SATCOM_TEAM_SIZE, len(vermelho_codes)))
    milsim_state["satcom"]["selected"] = {"azul": azul_satcom, "vermelho": vermelho_satcom}
    milsim_state["satcom"]["secondary"] = {
        "azul": [c for c in azul_codes if c not in azul_satcom],
        "vermelho": [c for c in vermelho_codes if c not in vermelho_satcom],
    }


def format_operator_list(codes, team):
    source = AZUL_OPERATOR_CODES if team == "azul" else VERMELHO_OPERATOR_CODES
    return "\n".join([f"▸ `{code}` — {source.get(code, 'OPERADOR')}" for code in codes]) if codes else "Sem operadores definidos."


def build_satcom_interference_embed():
    selected = milsim_state["satcom"]["selected"]["azul"]
    secondary = milsim_state["satcom"]["secondary"]["azul"]
    return tactical_embed(
        "⚠️ INTERFERÊNCIAS DETETADAS",
        "〔 TASK FORCE AZUL 〕\n\nO COMANDO CENTRAL detetou atividade eletrónica anormal nas comunicações da operação.\n\nA origem ainda não foi confirmada. A unidade deve manter-se em prontidão até nova ordem.",
        discord.Color.blue(),
        [
            {"name": "👥 EQUIPA DE RESPOSTA SATCOM", "value": format_operator_list(selected, "azul"), "inline": False},
            {"name": "📦 EQUIPA SECUNDÁRIA", "value": format_operator_list(secondary, "azul"), "inline": False},
            {"name": "📍 ORDEM", "value": "Os operadores SATCOM aguardam autorização de saída. Os restantes operadores ficam destacados para missão secundária.", "inline": False},
        ],
        footer="COMANDO CENTRAL • ALERTA DE INTERFERÊNCIAS",
    )


def build_satcom_red_initial_embed():
    selected = milsim_state["satcom"]["selected"]["vermelho"]
    secondary = milsim_state["satcom"]["secondary"]["vermelho"]
    return tactical_embed(
        "🔴 MISSÃO 04 — SATCOM BREACH",
        "〔 TASK FORCE VERMELHA 〕\n\nUma estação SATCOM clandestina foi localizada no terreno. Pode ser usada para comprometer as comunicações da Task Force Azul.\n\nApenas os operadores destacados estão autorizados a avançar para o terminal SATCOM.",
        discord.Color.red(),
        [
            {"name": "👥 OPERADORES SATCOM DESTACADOS", "value": format_operator_list(selected, "vermelho"), "inline": False},
            {"name": "📦 OPERADORES SECUNDÁRIOS", "value": format_operator_list(secondary, "vermelho"), "inline": False},
            {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Infiltrar terminal SATCOM\n▸ Iniciar hack com o código físico\n▸ Após início, defender posição durante 10 minutos", "inline": False},
            {"name": "⚠️ REGRA CRÍTICA", "value": "A missão SATCOM só termina se o hack completar por tempo ou se a Azul cancelar o hack. Sem respawn. Operadores SATCOM não podem interferir na secundária.", "inline": False},
        ],
        footer="COMANDO CENTRAL • OPERAÇÃO SATCOM 5x5",
    )


def build_satcom_blue_active_embed():
    selected = milsim_state["satcom"]["selected"]["azul"]
    return tactical_embed(
        "🚨 SATCOM COMPROMETIDO",
        "〔 TASK FORCE AZUL 〕\n\nO inimigo iniciou um hack ativo contra a rede SATCOM. A origem das interferências foi confirmada.\n\nApenas a equipa SATCOM destacada está autorizada a sair da base para localizar o terminal e interromper a sequência antes da conclusão.",
        discord.Color.light_grey(),
        [
            {"name": "👥 OPERADORES AUTORIZADOS", "value": format_operator_list(selected, "azul"), "inline": False},
            {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Localizar terminal SATCOM\n▸ Romper defesa inimiga\n▸ Validar cancelamento do hack com o código físico", "inline": False},
            {"name": "⚠️ CONDIÇÃO DE FIM", "value": "A missão termina apenas se a Azul cancelar o hack ou se o timer do hack chegar a 00:00. Sem respawn. Operadores SATCOM não podem interferir na secundária.", "inline": False},
        ],
        footer="COMANDO CENTRAL • SATCOM EM RISCO",
    )


async def start_satcom_hack():
    await stop_respawn_cycle()
    satcom = milsim_state["satcom"]
    if satcom.get("hack_active"):
        return
    satcom["hack_active"] = True
    satcom["hack_completed"] = False
    satcom["hack_cancelled"] = False
    satcom["hack_end_time"] = datetime.now(timezone.utc) + timedelta(seconds=SATCOM_HACK_SECONDS)
    for t in ["azul", "vermelho"]:
        milsim_state["mission_end_times"][t] = satcom["hack_end_time"]
        milsim_state["teams"][t]["phase"] = "mission"

    await send_team_embed_with_status_last(
        "vermelho",
        tactical_embed(
            "📡 HACK SATCOM INICIADO",
            "A sequência de intrusão SATCOM está ativa.\n\nA equipa destacada deve manter controlo da posição durante 10 minutos. O terminal não pode ser abandonado.",
            discord.Color.light_grey(),
            [
                {"name": "👥 OPERADORES SATCOM", "value": format_operator_list(satcom["selected"]["vermelho"], "vermelho"), "inline": False},
                {"name": "🎯 ORDEM", "value": "Defender terminal até o hack terminar.", "inline": False},
                {"name": "⚠️ CONDIÇÃO DE FIM", "value": "A missão termina por tempo ou se a Azul cancelar o hack. Sem respawn. Operadores SATCOM não podem interferir na secundária.", "inline": False},
            ],
            footer="COMANDO CENTRAL • HACK EM CURSO",
        ),
    )
    await send_team_embed_with_status_last("azul", build_satcom_blue_active_embed())
    satcom["hack_task"] = asyncio.create_task(satcom_hack_timer())
    asyncio.create_task(activate_satcom_secondary_missions())
    await milsim_log("📡 Hack SATCOM iniciado por BLACK-916. Timer de 10 minutos ativo.")
    await update_status_panel()



async def activate_satcom_secondary_missions():
    await asyncio.sleep(SATCOM_SECONDARY_DELAY)

    if not milsim_state.get("active"):
        return

    await stop_respawn_cycle()

    satcom = milsim_state.get("satcom", {})
    if not satcom.get("hack_active"):
        return

    satcom["secondary_active"] = True
    satcom["secondary_completed"] = False
    satcom["secondary_winner"] = None
    satcom["secondary_end_time"] = datetime.now(timezone.utc) + timedelta(seconds=SATCOM_SECONDARY_SECONDS)

    for t in ["azul", "vermelho"]:
        secondary = satcom.get("secondary", {}).get(t, [])
        color = discord.Color.blue() if t == "azul" else discord.Color.red()

        if t == "azul":
            title = "📦 MISSÃO SECUNDÁRIA — EMBOSCADA AO ACAMPAMENTO"
            desc = (
                "〔 TASK FORCE AZUL 〕\\n\\n"
                "A caixa de suprimentos Vermelha foi localizada no acampamento inimigo.\\n\\n"
                "A equipa secundária Azul tem autorização para iniciar uma emboscada, capturar a caixa e extrair até à base Azul."
            )
            objective = (
                "▸ Emboscar acampamento Vermelho\\n"
                "▸ Capturar caixa de suprimentos\\n"
                "▸ Extrair a caixa até à base Azul\\n"
                "▸ Validar `CACHE-777` antes dos 15 minutos"
            )
            win_condition = "A Azul vence a missão secundária apenas se validar `CACHE-777` na base Azul."
        else:
            title = "📦 MISSÃO SECUNDÁRIA — DEFESA DO ACAMPAMENTO"
            desc = (
                "〔 TASK FORCE VERMELHA 〕\\n\\n"
                "A caixa de suprimentos está armazenada no acampamento.\\n\\n"
                "A equipa secundária Vermelha deve defender a área e impedir a extração da caixa pela Task Force Azul."
            )
            objective = (
                "Defender a caixa de suprimentos do acampamento. e extração inimiga\\n"
                "▸ Resistir durante 15 minutos"
            )
            win_condition = "A Vermelha vence se a Azul não validar `CACHE-777` antes do fim do tempo."

        await send_team_embed_with_status_last(
            t,
            tactical_embed(
                title,
                desc,
                color,
                [
                    {"name": "👥 OPERADORES SECUNDÁRIOS", "value": format_operator_list(secondary, t), "inline": False},
                    {"name": "📌 OBJETIVO DA MISSÃO", "value": objective, "inline": False},
                    {"name": "⏱️ TEMPO", "value": "**15 minutos**", "inline": True},
                    {"name": "⚠️ MORTE SÚBITA", "value": "Após atendimento médico, se o operador voltar a ser eliminado, fica fora até à próxima missão. Sem respawn.", "inline": False},
                    {"name": "🚫 RESTRIÇÃO", "value": "Operadores da secundária não podem interferir na SATCOM. Operadores SATCOM não podem interferir na secundária.", "inline": False},
                    {"name": "🏁 CONDIÇÃO DE VITÓRIA", "value": win_condition, "inline": False},
                ],
                footer="COMANDO CENTRAL • MISSÃO SECUNDÁRIA 5x5"
            )
        )

    await create_secondary_timer_panel("azul")
    await create_secondary_timer_panel("vermelho")
    satcom["secondary_task"] = asyncio.create_task(satcom_secondary_timer())

    await milsim_log("📦 Missão secundária do acampamento ativada após 3 minutos. Timer de 15 minutos iniciado.")


async def satcom_secondary_timer():
    while milsim_state.get("active"):
        satcom = milsim_state.get("satcom", {})
        if not satcom.get("secondary_active") or satcom.get("secondary_completed"):
            return

        end_time = satcom.get("secondary_end_time")
        if not end_time:
            return

        remaining = end_time - datetime.now(timezone.utc)
        if remaining.total_seconds() <= 0:
            break

        await update_status_panel()
        await asyncio.sleep(min(5, max(1, int(remaining.total_seconds()))))

    satcom = milsim_state.get("satcom", {})
    if not satcom.get("secondary_active") or satcom.get("secondary_completed"):
        return

    satcom["secondary_active"] = False
    satcom["secondary_completed"] = True
    satcom["secondary_winner"] = "vermelho"
    await delete_all_secondary_timer_panels()
    milsim_state["scores"]["vermelho"] += 10

    await milsim_send_to_team(
        "vermelho",
        embed=tactical_embed(
            "📦 SECUNDÁRIA CONCLUÍDA — ACAMPAMENTO DEFENDIDO",
            "A Task Force Azul não conseguiu capturar e registar a caixa de suprimentos dentro dos 15 minutos.",
            discord.Color.green(),
            [
                {"name": "🏆 Resultado", "value": "Vitória secundária Vermelha", "inline": False},
                {"name": "🏆 Pontos", "value": "**+10 pontos atribuídos**", "inline": True}
            ],
            footer="COMANDO CENTRAL • RESULTADO SECUNDÁRIO"
        )
    )

    await milsim_send_to_team(
        "azul",
        embed=tactical_embed(
            "📦 SECUNDÁRIA FALHADA — EXTRAÇÃO NÃO CONFIRMADA",
            "A caixa de suprimentos não foi registada na base Azul dentro do tempo limite.",
            discord.Color.red(),
            [
                {"name": "📍 Ordem", "value": "Operadores secundários regressam à base e aguardam próxima janela operacional.", "inline": False}
            ],
            footer="COMANDO CENTRAL • RESULTADO SECUNDÁRIO"
        )
    )

    await milsim_log("📦 Missão secundária terminou por tempo. Vermelho venceu defesa do acampamento. +10 pontos.")
    await update_status_panel()


async def satcom_hack_timer():
    while milsim_state.get("active"):
        satcom = milsim_state.get("satcom", {})
        if not satcom.get("hack_active") or satcom.get("hack_cancelled"):
            return

        end_time = satcom.get("hack_end_time")
        if not end_time:
            return

        remaining = end_time - datetime.now(timezone.utc)
        if remaining.total_seconds() <= 0:
            break

        await update_status_panel()
        await asyncio.sleep(min(5, max(1, int(remaining.total_seconds()))))

    satcom = milsim_state.get("satcom", {})
    if not satcom.get("hack_active") or satcom.get("hack_cancelled"):
        return

    satcom["hack_active"] = False
    satcom["hack_completed"] = True
    satcom["hack_cancelled"] = False

    milsim_state["scores"]["vermelho"] += 20

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "regroup"
        milsim_state["teams"][t]["regrouped"] = False

    await stop_respawn_cycle()

    await send_team_embed_plain(
        "vermelho",
        tactical_embed(
            "✅ Ordem de Retirada - Missão Bem sucedida (SATCOM BREACH)",
            "📡 O hack SATCOM foi concluído com sucesso. As comunicações inimigas foram comprometidas e a operação Vermelha atingiu o objetivo principal.\\n\\n"
            "📌 **Motivo**\nSATCOM COMPROMETIDO\\n"
            "A Task Force Vermelha manteve controlo do terminal até ao fim da janela de hack.\\n\\n"
            "Estejam em alerta e aguardem novas ordens!",
            discord.Color.red(),
            footer="COMANDO CENTRAL • ORDEM DE RETIRADA"
        )
    )

    await send_team_embed_plain(
        "azul",
        tactical_embed(
            "⚠️ Ordem de Retirada - Missão Fracassada! (SATCOM BREACH)",
            "📡 Regressem de imediato ao HQ, reorganizem a equipa e preparem-se para novas ordens.\\n\\n"
            "📌 **Motivo**\nSATCOM COMPROMETIDO\\n"
            "A Task Force Vermelha concluiu o hack SATCOM antes da Azul conseguir cancelar a transmissão.\\n\\n"
            "Estejam em alerta e aguardem novas ordens!",
            discord.Color.blue(),
            footer="COMANDO CENTRAL • ORDEM DE RETIRADA"
        )
    )

    await milsim_log("📡 Hack SATCOM concluído por tempo. Vitória Vermelha na Missão 4. +20 pontos.")
    await update_status_panel()


async def cancel_satcom_hack():
    satcom = milsim_state.get("satcom", {})

    if not satcom.get("hack_active"):
        return False

    satcom["hack_active"] = False
    satcom["hack_cancelled"] = True
    satcom["hack_completed"] = False

    task = satcom.get("hack_task")
    if task and not task.done():
        task.cancel()

    milsim_state["scores"]["azul"] += 20

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "regroup"
        milsim_state["teams"][t]["regrouped"] = False

    await stop_respawn_cycle()

    await send_team_embed_plain(
        "azul",
        tactical_embed(
            "✅ Ordem de Retirada - Missão Bem sucedida (SATCOM BREACH)",
            "📡 O hack SATCOM foi cancelado com sucesso. As comunicações Azul foram preservadas.\\n\\n"
            "📌 **Motivo**\nHACK SATCOM CANCELADO\\n"
            "A Task Force Azul localizou o terminal e interrompeu a sequência de hackeamento.\\n\\n"
            "Estejam em alerta e aguardem novas ordens!",
            discord.Color.blue(),
            footer="COMANDO CENTRAL • ORDEM DE RETIRADA"
        )
    )

    await send_team_embed_plain(
        "vermelho",
        tactical_embed(
            "⚠️ Ordem de Retirada - Missão Fracassada! (SATCOM BREACH)",
            "📡 Regressem de imediato ao HQ, reorganizem a equipa e preparem-se para novas ordens.\\n\\n"
            "📌 **Motivo**\nHACK SATCOM INTERROMPIDO\\n"
            "A Task Force Azul localizou o terminal e cancelou a operação SATCOM antes da conclusão.\\n\\n"
            "Estejam em alerta e aguardem novas ordens!",
            discord.Color.red(),
            footer="COMANDO CENTRAL • ORDEM DE RETIRADA"
        )
    )

    await milsim_log("📡 SATCOM cancelado pela Task Force Azul. Vitória Azul na Missão 4. +20 pontos.")
    await update_status_panel()
    return True


MISSION_CODES = {
    "SHADOW-214": {
        "team": "azul",
        "mission": "mission_1",
        "points": 10,
        "type": "complete",
        "branch": "standard",
        "embed": lambda: tactical_embed(
            "✅ DISCO VERDADEIRO CONFIRMADO",
            "A caixa segura regressou ao HQ e o disco verdadeiro foi identificado. A inteligência principal ainda está intacta.\n\nA próxima janela operacional será transmitida quando o tempo da missão terminar.",
            discord.Color.blue(),
            [
                {"name": "📍 ORDEM", "value": "Regressem ao HQ, iniciem descanso operacional e preparem-se para novas ordens.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+10 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "⚠️ INTEL AZUL EXTRAÍDA",
            "A Task Force Azul conseguiu retirar a caixa segura e identificar o disco verdadeiro.\n\nPreparem-se para impedir o envio dos dados.",
            discord.Color.red()
        )
    },

    "VIPER-771": {
        "team": "vermelho",
        "mission": "mission_1",
        "points": 10,
        "type": "complete",
        "branch": "compromised",
        "embed": lambda: tactical_embed(
            "💥 CAIXA COMPROMETIDA",
            "A caixa segura foi levada até à base Vermelha e o conteúdo foi comprometido com sucesso.\n\nA inteligência principal da Azul deixou de ser confiável.",
            discord.Color.red(),
            [
                {"name": "📍 ORDEM", "value": "Reagrupem e preparem bloqueio às tentativas de recuperação inimiga.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+10 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "🚨 CAIXA SEGURA COMPROMETIDA",
            "A Task Force Vermelha comprometeu a caixa segura. O disco principal já não é confiável.\n\nAinda poderá existir uma hipótese de recuperar fragmentos de backup.",
            discord.Color.orange()
        )
    },

    "BUNKER-551": {
        "team": "azul",
        "mission": "mission_2",
        "points": 0,
        "type": "decryption",
        "embed": lambda: tactical_embed(
            "📡 ENVIO DE DADOS INICIADO",
            "A caixa segura foi posicionada junto ao terminal do BUNKER e o uplink militar foi ativado.\n\nA transmissão para a central começou. Defendam o terminal durante toda a sequência.",
            discord.Color.blue(),
            [
                {"name": "⏱️ TEMPO ESTIMADO", "value": "**10 MINUTOS**", "inline": True},
                {"name": "📍 ORDEM", "value": "Segurem o BUNKER. Todas as forças hostis irão convergir para esta posição.", "inline": False},
                {"name": "🏆 PONTOS", "value": "Os pontos são atribuídos se a transmissão for concluída.", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "🚨 TRANSMISSÃO AZUL DETETADA",
            "A Task Force Azul iniciou envio de dados no BUNKER.\n\nSe a sequência for concluída, a rede operacional Vermelha ficará comprometida.",
            discord.Color.red(),
            [
                {"name": "🎯 OBJETIVO PRIORITÁRIO", "value": "Infiltrar o BUNKER e interromper a transmissão.", "inline": False}
            ]
        )
    },

    "RAVEN-119": {
        "team": "vermelho",
        "mission": "mission_2",
        "points": 15,
        "type": "complete",
        "embed": lambda: tactical_embed(
            "💥 TRANSMISSÃO INTERROMPIDA",
            "A sequência de envio foi sabotada. O uplink caiu antes da conclusão e os dados não chegaram à central.",
            discord.Color.red(),
            [
                {"name": "📍 ORDEM", "value": "Regressem ao COMANDO para reorganização.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+15 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "❌ ENVIO DE DADOS FALHADO",
            "A transmissão foi interrompida por sabotagem inimiga.\n\nRegressem ao COMANDO e reorganizem a unidade.",
            discord.Color.orange()
        )
    },

    "FRAGMENT-404": {
        "team": "azul",
        "mission": "mission_2",
        "points": 20,
        "type": "complete",
        "embed": lambda: tactical_embed(
            "✅ FRAGMENTOS RECONSTRUÍDOS",
            "Os três fragmentos de backup foram reunidos e a inteligência foi parcialmente restaurada.\n\nA operação sofreu danos, mas ainda existe informação suficiente para continuar.",
            discord.Color.blue(),
            [
                {"name": "📍 ORDEM", "value": "Regressem ao COMANDO. Próxima fase operacional será recalculada com os dados recuperados.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+20 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "⚠️ DADOS PARCIALMENTE RECUPERADOS",
            "A Task Force Azul conseguiu reconstruir fragmentos de backup. A inteligência não foi totalmente destruída.",
            discord.Color.red()
        )
    },


    "HIJACK-515": {
        "team": "azul",
        "mission": "mission_3",
        "points": 15,
        "type": "complete",
        "embed": lambda: tactical_embed(
            "🚛 CONVOY INTERCEPTADO",
            "A Task Force Azul conseguiu capturar a carga do comboio inimigo e regressar com o material operacional.\n\n"
            "A logística Vermelha foi comprometida.",
            discord.Color.blue(),
            [
                {"name": "📍 ORDEM", "value": "Regressar ao HQ, proteger a carga recuperada e aguardar novas ordens.", "inline": False},
                {"name": "🏆 PONTOS", "value": "+15 pontos atribuídos", "inline": False}
            ]
        )
    },

    "RED-CP1": {"team": "vermelho", "mission": "mission_3", "points": 0, "type": "checkpoint", "step": 1, "embed": lambda: tactical_embed("✅ CHECKPOINT 1 VALIDADO", "A carga passou pelo primeiro ponto da rota obrigatória.", discord.Color.red(), [{"name": "➡️ PRÓXIMO PASSO", "value": "Avancem para o checkpoint seguinte.", "inline": False}])},
    "RED-CP2": {"team": "vermelho", "mission": "mission_3", "points": 0, "type": "checkpoint", "step": 2, "embed": lambda: tactical_embed("✅ CHECKPOINT 2 VALIDADO", "A carga continua em movimento. A pressão inimiga deverá aumentar.", discord.Color.red(), [{"name": "➡️ PRÓXIMO PASSO", "value": "Continuem a rota.", "inline": False}])},
    "RED-CP3": {"team": "vermelho", "mission": "mission_3", "points": 0, "type": "checkpoint", "step": 3, "embed": lambda: tactical_embed("✅ CHECKPOINT 3 VALIDADO", "Metade crítica da rota foi ultrapassada. Mantenham escolta apertada.", discord.Color.red(), [{"name": "➡️ PRÓXIMO PASSO", "value": "Avancem para o próximo ponto.", "inline": False}])},
    "RED-CP4": {"team": "vermelho", "mission": "mission_3", "points": 0, "type": "checkpoint", "step": 4, "embed": lambda: tactical_embed("✅ CHECKPOINT 4 VALIDADO", "A carga aproxima-se da extração final. Esperem tentativa de interceção máxima.", discord.Color.red(), [{"name": "➡️ PRÓXIMO PASSO", "value": "Último checkpoint antes da extração.", "inline": False}])},
    "RED-CP5": {"team": "vermelho", "mission": "mission_3", "points": 0, "type": "checkpoint", "step": 5, "embed": lambda: tactical_embed("✅ CHECKPOINT 5 VALIDADO", "Rota completa. Procedam para extração final.", discord.Color.red(), [{"name": "➡️ PRÓXIMO PASSO", "value": "Validem a extração final no ponto indicado.", "inline": False}])},

    "GHOST-802": {
        "team": "azul",
        "mission": "mission_3",
        "points": 25,
        "type": "complete",
        "embed": lambda: tactical_embed(
            "📦 CARGA INTERCETADA E DEPOSITADA",
            "A carga Vermelha foi intercetada, capturada e depositada no ponto indicado.\n\nA linha logística inimiga foi comprometida.",
            discord.Color.blue(),
            [
                {"name": "📍 ORDEM", "value": "Regressem ao COMANDO para reorganização.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+25 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "🚨 CARGA PERDIDA",
            "A Task Force Azul intercetou e depositou a carga. A rota logística Vermelha foi comprometida.",
            discord.Color.red()
        )
    },

    "EXFIL-337": {
        "team": "vermelho",
        "mission": "mission_3",
        "points": 25,
        "type": "complete",
        "requires_step": 5,
        "embed": lambda: tactical_embed(
            "✅ EXTRAÇÃO CONCLUÍDA",
            "A carga atravessou os cinco checkpoints e chegou ao destino final.\n\nA rota foi mantida apesar da pressão inimiga.",
            discord.Color.red(),
            [
                {"name": "📍 ORDEM", "value": "Regressem ao COMANDO para nova janela operacional.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+25 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "⚠️ TRANSPORTE INIMIGO CONCLUÍDO",
            "A Task Force Vermelha completou a rota de transporte e entregou a carga.",
            discord.Color.blue()
        )
    },

    "BLACK-916": {
        "team": "vermelho",
        "mission": "mission_4",
        "points": 0,
        "type": "satcom_start",
        "embed": lambda: tactical_embed(
            "📡 HACK SATCOM INICIADO",
            "A estação SATCOM foi ativada e a sequência de intrusão começou.\n\nDefendam o terminal. A partir deste momento, a Azul irá tentar localizar a origem das interferências.",
            discord.Color.light_grey(),
            [
                {"name": "📍 ORDEM", "value": "Manter posição e proteger o uplink até conclusão operacional.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+20 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "⚠️ ALERTA OPERACIONAL — SATCOM",
            "〔 TASK FORCE AZUL 〕\n\nInterferências críticas foram detetadas na rede de comunicações da operação. As leituras indicam atividade hostil proveniente de uma estação SATCOM clandestina algures dentro do complexo.\n\nSe o inimigo concluir o hack, todas as comunicações Azul poderão ficar comprometidas, movimentações táticas poderão ser expostas e frequências rádio poderão ser intercetadas.",
            discord.Color.blue(),
            [
                {"name": "👥 OPERADORES DESIGNADOS", "value": "▸ Player1\n▸ Player2\n▸ Player3\n▸ Player4\n▸ Player5", "inline": False},
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Localizar origem das interferências\n▸ Eliminar operadores hostis\n▸ Interromper hackeamento\n▸ Recuperar controlo das comunicações", "inline": False},
                {"name": "📡 ORDEM", "value": "Cada minuto perdido aproxima o inimigo da vitória operacional. Encontrem-nos antes que seja tarde.", "inline": False}
            ]
        )
    },

    "OMEGA-440": {
        "team": "azul",
        "mission": "mission_4",
        "points": 0,
        "type": "satcom_cancel",
        "embed": lambda: tactical_embed(
            "✅ HACK CANCELADO",
            "A equipa de resposta localizou a estação SATCOM e interrompeu a sequência de intrusão.\n\nAs comunicações Azul foram preservadas.",
            discord.Color.blue(),
            [
                {"name": "📍 ORDEM", "value": "Regressem ao COMANDO e preparem-se para a fase final.", "inline": False},
                {"name": "🏆 PONTOS", "value": "**+20 pontos atribuídos**", "inline": False}
            ]
        ),
        "enemy_alert_embed": lambda: tactical_embed(
            "❌ HACK SATCOM INTERROMPIDO",
            "A Task Force Azul localizou a estação e cancelou a sequência de intrusão.",
            discord.Color.red()
        )
    },

    "CACHE-777": {
        "team": "azul",
        "mission": "mission_4",
        "points": 10,
        "type": "secondary",
        "embed": lambda: tactical_embed(
            "📦 SUPPLY CACHE RECUPERADA",
            "A equipa Azul executou a emboscada e extraiu recursos do acampamento avançado inimigo.",
            discord.Color.blue(),
            [{"name": "🏆 PONTOS", "value": "**+10 pontos atribuídos**", "inline": False}]
        )
    },

    "NOVA-999": {
        "team": "azul",
        "mission": "final",
        "points": 30,
        "type": "end",
        "embed": lambda: tactical_embed("🏆 DOMINAÇÃO AZUL", "A Task Force Azul dominou o CQB e assumiu controlo da operação.", discord.Color.blue())
    },

    "IRON-666": {
        "team": "vermelho",
        "mission": "final",
        "points": 30,
        "type": "end",
        "embed": lambda: tactical_embed("🏆 DOMINAÇÃO VERMELHA", "A Task Force Vermelha dominou o CQB e assumiu controlo da operação.", discord.Color.red())
    }
}

NEXT_MISSIONS = {
    "mission_1": {
        "azul": lambda: tactical_embed(
            "🔵 MISSÃO 02 — DATA TRANSFER" if milsim_state.get("mission_branch") != "compromised" else "🔵 MISSÃO 02 — RECOVER FRAGMENTS",
            (
                "〔 TASK FORCE AZUL 〕\n\nO disco verdadeiro foi confirmado. A inteligência recuperada contém informação suficiente para comprometer operações inimigas em toda a região. Mas os dados ainda não estão seguros.\n\nMovimentação inimiga foi detetada perto do BUNKER. A partir do momento em que a transmissão começar, todas as forças hostis irão convergir para a vossa posição."
                if milsim_state.get("mission_branch") != "compromised" else
                "〔 TASK FORCE AZUL 〕\n\nA caixa segura foi comprometida. O disco principal já não é confiável.\n\nMas antes da operação falhar completamente, fragmentos de backup foram escondidos em diferentes zonas do complexo. Cada fragmento recuperado aumenta as hipóteses de restaurar os dados perdidos."
            ),
            discord.Color.blue(),
            (
                [
                    {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Transportar a caixa segura até ao BUNKER\n▸ Ativar uplink militar\n▸ Iniciar transmissão para a central\n▸ Defender o terminal durante toda a sequência", "inline": False},
                    {"name": "⚠️ REGRAS OPERACIONAIS", "value": "▸ A caixa deve permanecer junto do terminal\n▸ O uplink demora 10 minutos até concluir\n▸ Se o terminal cair, a transmissão poderá falhar", "inline": False},
                    {"name": "📡 ORDEM", "value": "Segurem o BUNKER. Defendam o terminal. E enviem os dados antes que seja tarde.", "inline": False}
                ]
                if milsim_state.get("mission_branch") != "compromised" else
                [
                    {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Recuperar fragmento no BUNKER\n▸ Recuperar fragmento no CQB\n▸ Recuperar fragmento no ACAMPAMENTO\n▸ Reconstruir parcialmente a inteligência", "inline": False},
                    {"name": "📡 ORDEM", "value": "Não deixem que esta operação termine aqui. Movam-se rápido e recuperem o que ainda resta.", "inline": False}
                ]
            )
        ),
        "vermelho": lambda: tactical_embed(
            "🔴 MISSÃO 02 — SIGNAL BREAK" if milsim_state.get("mission_branch") != "compromised" else "🔴 MISSÃO 02 — DENY RECOVERY",
            (
                "〔 TASK FORCE VERMELHA 〕\n\nA Task Force Azul conseguiu recuperar o disco principal e está agora a tentar transmitir os dados para a central através do BUNKER.\n\nSe conseguirem concluir a transmissão, toda a nossa rede operacional ficará comprometida, posições estratégicas serão expostas e a operação poderá ficar perdida."
                if milsim_state.get("mission_branch") != "compromised" else
                "〔 TASK FORCE VERMELHA 〕\n\nApesar da caixa Azul ter sido comprometida, existe possibilidade de tentarem restaurar parte da inteligência através de fragmentos escondidos no complexo.\n\nA Azul está fragilizada, mas ainda é perigosa. Neguem-lhes qualquer hipótese de recuperação."
            ),
            discord.Color.red(),
            (
                [
                    {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Infiltrar o BUNKER\n▸ Interromper a transmissão\n▸ Destruir ou recuperar a caixa segura\n▸ Impedir envio dos dados", "inline": False},
                    {"name": "📡 INTEL", "value": "O uplink demora 10 minutos até concluir. Esse é o vosso tempo limite. Cada minuto perdido aproxima a Azul da vitória operacional.", "inline": False},
                    {"name": "📍 ORDEM", "value": "Cortem a transmissão.", "inline": False}
                ]
                if milsim_state.get("mission_branch") != "compromised" else
                [
                    {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Impedir recolha dos fragmentos\n▸ Defender zonas críticas\n▸ Atrasar recuperação da inteligência", "inline": False},
                    {"name": "📍 ORDEM", "value": "Acabem com isto de vez.", "inline": False}
                ]
            )
        )
    },
    "mission_2": {
        "azul": lambda: tactical_embed(
            "🔵 MISSÃO 03 — INTERCEPT CONVOY",
            "〔 TASK FORCE AZUL 〕\n\nUma unidade Vermelha está a transportar material crítico através do complexo operacional. Acreditamos que a carga contém equipamento capaz de alterar o rumo da operação.\n\nA Vermelha irá defender a carga com tudo o que tem. Ataquem rápido, criem confusão e não deixem o comboio escapar.",
            discord.Color.blue(),
            [
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Localizar o transporte inimigo\n▸ Intercetar a carga\n▸ Eliminar escoltas\n▸ Capturar o material\n▸ Levar a carga até ao BUNKER", "inline": False},
                {"name": "⚠️ REGRAS OPERACIONAIS", "value": "▸ O operador da carga não pode correr\n▸ Se eliminado, a carga permanece no terreno", "inline": False}
            ]
        ),
        "vermelho": lambda: tactical_embed(
            "🔴 MISSÃO 03 — CONVOY RUN",
            "〔 TASK FORCE VERMELHA 〕\n\nMaterial operacional altamente sensível precisa de atravessar o complexo através de uma rota obrigatória. A rota foi comprometida. Interceção inimiga é esperada a qualquer momento.\n\nReconhecimento indica movimentação Azul nos flancos do percurso. Esperem emboscadas, ataques rápidos e combate constante até à extração final.",
            discord.Color.red(),
            [
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Proteger a carga\n▸ Validar os 5 checkpoints obrigatórios\n▸ Manter integridade do transporte\n▸ Concluir extração final", "inline": False},
                {"name": "⚠️ REGRAS OPERACIONAIS", "value": "▸ O operador da carga não pode correr\n▸ Se eliminado, a carga permanece no local\n▸ A rota deve ser seguida por ordem. Devem validar 5 checkpoints antes da extração final", "inline": False},
                {"name": "📍 ORDEM", "value": "Mantenham o comboio em movimento.", "inline": False}
            ]
        )
    },
    "mission_3": {
        "azul": lambda: tactical_embed(
            "🔵 MISSÃO 04 — AGUARDAR ALERTA SATCOM",
            "〔 TASK FORCE AZUL 〕\n\nA rede de comunicações está instável e o COMANDO mantém monitorização ativa.\n\nPermaneçam em prontidão. Se forem detetadas interferências críticas, uma unidade reduzida será destacada para resposta imediata.",
            discord.Color.blue(),
            [
                {"name": "📡 ORDEM", "value": "Mantenham equipamento pronto e aguardem alerta operacional.", "inline": False}
            ]
        ),
        "vermelho": lambda: tactical_embed(
            "🔴 MISSÃO 04 — SATCOM DOMINATION",
            "〔 TASK FORCE VERMELHA 〕\n\nUma estação SATCOM militar abandonada foi localizada dentro do complexo operacional. Após análise dos sistemas encontrados no terreno, foi confirmado que a infraestrutura ainda se encontra parcialmente funcional.\n\nSe conseguirmos assumir controlo da estação, poderemos comprometer comunicações Azul, intercetar transmissões, localizar movimentações inimigas e controlar informação operacional em tempo real.",
            discord.Color.red(),
            [
                {"name": "👥 OPERADORES DESIGNADOS", "value": "▸ Player1\n▸ Player2\n▸ Player3\n▸ Player4\n▸ Player5", "inline": False},
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Infiltrar estação SATCOM\n▸ Iniciar hackeamento\n▸ Defender terminal até conclusão", "inline": False},
                {"name": "⚠️ REGRAS OPERACIONAIS", "value": "▸ Apenas os operadores designados abandonam a base\n▸ Evitar deteção prematura\n▸ Manter silêncio rádio sempre que possível", "inline": False},
                {"name": "📡 ORDEM", "value": "Movam-se sem serem vistos.", "inline": False}
            ]
        )
    },
    "mission_4": {
        "azul": lambda: tactical_embed(
            "⚫ MISSÃO FINAL — TOTAL DOMINATION",
            "〔 TRANSMISSÃO PRIORITÁRIA • COMANDO CENTRAL 〕\n\nTudo o que aconteceu até agora conduziu-nos a este momento. Reconhecimento confirmou que o complexo CQB se tornou o último ponto estratégico ativo da operação.\n\nEsperem combate intenso, contra-ataques constantes e guerra total dentro da estrutura. Hoje não lutam apenas pela vitória. Lutam pelos operadores ao vosso lado e por cada missão sobrevivida até aqui.",
            discord.Color.dark_gold(),
            [
                {"name": "📍 TERMINAIS ELETRÓNICOS", "value": "▸ ALPHA — FLANCO ESQUERDO\n▸ BRAVO — CENTRO DO CQB\n▸ CHARLIE — FLANCO DIREITO", "inline": False},
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Ativar terminais para a vossa equipa\n▸ Manter os timers a contar a vosso favor\n▸ Parar os timers inimigos\n▸ Dominar o CQB até ao final da operação", "inline": False},
                {"name": "⚙️ MECÂNICA", "value": "Cada terminal possui um dispositivo timer eletrónico. Os operadores devem ativar o timer da sua equipa e, sempre que possível, parar o timer inimigo.", "inline": False}
            ]
        ),
        "vermelho": lambda: tactical_embed(
            "⚫ MISSÃO FINAL — TOTAL DOMINATION",
            "〔 TRANSMISSÃO PRIORITÁRIA • COMANDO CENTRAL 〕\n\nTudo o que aconteceu até agora conduziu-nos a este momento. Reconhecimento confirmou que o complexo CQB se tornou o último ponto estratégico ativo da operação.\n\nEsperem combate intenso, contra-ataques constantes e guerra total dentro da estrutura. Hoje não lutam apenas pela vitória. Lutam pelos operadores ao vosso lado e por cada missão sobrevivida até aqui.",
            discord.Color.dark_gold(),
            [
                {"name": "📍 TERMINAIS ELETRÓNICOS", "value": "▸ ALPHA — FLANCO ESQUERDO\n▸ BRAVO — CENTRO DO CQB\n▸ CHARLIE — FLANCO DIREITO", "inline": False},
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Ativar terminais para a vossa equipa\n▸ Manter os timers a contar a vosso favor\n▸ Parar os timers inimigos\n▸ Dominar o CQB até ao final da operação", "inline": False},
                {"name": "⚙️ MECÂNICA", "value": "Cada terminal possui um dispositivo timer eletrónico. Os operadores devem ativar o timer da sua equipa e, sempre que possível, parar o timer inimigo.", "inline": False}
            ]
        )
    }
}



NEXT_MISSIONS_ALT = {
    "mission_1_compromised": {
        "azul": lambda: tactical_embed(
            "🔵 MISSÃO 02 — RECOVER FRAGMENTS",
            "〔 TASK FORCE AZUL 〕\n\n"
            "A caixa segura não foi assegurada dentro da janela operacional. A inteligência principal foi perdida ou fragmentada no terreno.\n\n"
            "Ainda existem backups parciais espalhados pelo complexo. Esta é a última oportunidade de restaurar parte dos dados.",
            discord.Color.blue(),
            [
                {
                    "name": "📌 OBJETIVO DA MISSÃO",
                    "value": (
                        "▸ Recuperar fragmento no BUNKER\n"
                        "▸ Recuperar fragmento no CQB\n"
                        "▸ Recuperar fragmento no ACAMPAMENTO\n"
                        "▸ Juntar os três fragmentos\n"
                        "▸ Validar o código reconstruído"
                    ),
                    "inline": False
                },
                {
                    "name": "📡 INTEL",
                    "value": "As forças Vermelhas também sabem que os fragmentos existem. Esperem resistência nos três setores.",
                    "inline": False
                },
                {
                    "name": "📍 ORDEM",
                    "value": "Movam-se rápido. Cada fragmento pode ser a diferença entre recuperar a operação ou perder a inteligência para sempre.",
                    "inline": False
                }
            ]
        ),
        "vermelho": lambda: tactical_embed(
            "🔴 MISSÃO 02 — DENY RECOVERY",
            "〔 TASK FORCE VERMELHA 〕\n\n"
            "Nenhuma força conseguiu assegurar a caixa segura. A inteligência foi fragmentada e espalhada pelo terreno.\n\n"
            "A Azul irá tentar recuperar esses fragmentos para reconstruir parte dos dados.",
            discord.Color.red(),
            [
                {
                    "name": "📌 OBJETIVO DA MISSÃO",
                    "value": (
                        "▸ Impedir recolha dos fragmentos\n"
                        "▸ Defender setores críticos\n"
                        "▸ Atrasar movimentação Azul\n"
                        "▸ Negar recuperação parcial da inteligência"
                    ),
                    "inline": False
                },
                {
                    "name": "📡 INTEL",
                    "value": "Atividade provável nos setores BUNKER, CQB e ACAMPAMENTO.",
                    "inline": False
                },
                {
                    "name": "📍 ORDEM",
                    "value": "Não lhes permitam recuperar aquilo que a janela operacional destruiu.",
                    "inline": False
                }
            ]
        )
    }
}

def milsim_get_channel(channel_id: int):
    return bot.get_channel(channel_id)


def milsim_team_from_channel(channel_id: int):
    if channel_id == AZUL_CHANNEL_ID:
        return "azul"
    if channel_id == VERMELHO_CHANNEL_ID:
        return "vermelho"
    return None


def milsim_enemy(team: str):
    return "vermelho" if team == "azul" else "azul"


def milsim_channel_for_team(team: str):
    return milsim_get_channel(AZUL_CHANNEL_ID if team == "azul" else VERMELHO_CHANNEL_ID)


async def milsim_log(texto: str):
    logs = milsim_get_channel(LOGS_CHANNEL_ID)
    if logs:
        await logs.send(texto)


async def should_show_team_timer(team: str) -> bool:
    """Mostra/recria o painel de timer só quando existe uma operação ativa e timer real guardado."""
    return bool(
        milsim_state.get("active")
        and milsim_state.get("mission_end_times", {}).get(team)
    )


async def prepare_team_channel_for_new_milsim_embed(team: str):
    """Limpa timers antigos antes de publicar um novo embed Milsim no canal da equipa.

    Não altera mission_end_times, por isso o cronómetro real da missão não reinicia.
    """
    await cleanup_team_timer_panels(team)


async def finish_team_channel_after_new_milsim_embed(team: str):
    """Recria o painel visual de timer depois de publicar um novo embed Milsim."""
    if await should_show_team_timer(team):
        await create_team_timer_panel(team)


async def milsim_send_to_team(team: str, embed=None, content=None):
    canal = milsim_channel_for_team(team)
    if canal:
        if embed:
            await prepare_team_channel_for_new_milsim_embed(team)
            embed = apply_medical_rules_to_embed(embed)
            msg = await canal.send(embed=embed)
            await finish_team_channel_after_new_milsim_embed(team)
            return msg
        elif content:
            return await canal.send(content)


def format_respawn_remaining():
    respawn = milsim_state.get("respawn", {})
    next_at = respawn.get("next_respawn_at")

    if not respawn.get("active") or not next_at:
        return "Inativo"

    now = datetime.now(timezone.utc)
    remaining = next_at - now

    if remaining.total_seconds() <= 0:
        return "00:00"

    total_seconds = int(remaining.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def build_respawn_panel_embed():
    respawn = milsim_state.get("respawn", {})
    active = respawn.get("active", False)
    mission = respawn.get("mission") or "Sem missão ativa"
    next_at = respawn.get("next_respawn_at")

    if active and next_at:
        remaining = next_at - datetime.now(timezone.utc)
        if remaining.total_seconds() <= RESPAWN_OPEN_SECONDS:
            status = "🟢 RESSURGIMENTO AUTORIZADO"
            desc = (
                "Janela de ressurgimento aberta.\n\n"
                "Jogadores em base autorizados a regressar ao jogo."
            )
        else:
            status = "🟡 A AGUARDAR PRÓXIMA JANELA"
            desc = "Jogadores em base devem aguardar a próxima vaga de ressurgimento."
    else:
        status = "⚫ RESPawns INATIVOS"
        desc = "O ciclo de ressurgimento ainda não está ativo."

    embed = tactical_embed(
        "♻️ PAINEL DE RESSURGIMENTO",
        desc,
        discord.Color.green() if active else discord.Color.dark_grey(),
        [
            {"name": "📡 Missão Atual", "value": f"`{mission}`", "inline": True},
            {"name": "📌 Estado", "value": status, "inline": True},
            {"name": "⏱️ Próxima Janela", "value": f"**{format_respawn_remaining()}**", "inline": False},
            {
                "name": "📍 Regras de Respawn",
                "value": (
                    "▸ Ciclos de ressurgimento a cada **5 minutos**\n"
                    "▸ Respawn apenas na base\n"
                    "▸ Jogadores eliminados/capturados aguardam próxima vaga\n"
                    "▸ O ciclo reinicia sempre que uma nova missão começa"
                ),
                "inline": False
            }
        ],
        footer="COMANDO CENTRAL • CONTROLO DE RESSURGIMENTO"
    )

    return embed


async def update_respawn_panel():
    current_mission = milsim_state["teams"]["azul"].get("current")
    if not respawn_allowed_for_mission(current_mission):
        return

    channel = milsim_get_channel(COMANDO_CHANNEL_ID)
    if not channel:
        return

    respawn = milsim_state.setdefault("respawn", {})
    message_id = respawn.get("panel_message_id")

    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=build_respawn_panel_embed())
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            respawn["panel_message_id"] = None

    try:
        msg = await channel.send(embed=build_respawn_panel_embed())
        respawn["panel_message_id"] = msg.id
    except (discord.Forbidden, discord.HTTPException):
        pass


async def respawn_panel_loop(mission_name: str):
    while milsim_state.get("active") and milsim_state.get("respawn", {}).get("active"):
        respawn = milsim_state.get("respawn", {})

        if respawn.get("mission") != mission_name:
            return

        # Parar respawn quando as duas equipas já estão em reagrupamento.
        if all(milsim_state["teams"][t].get("phase") == "regroup" for t in ["azul", "vermelho"]):
            respawn["active"] = False
            await update_respawn_panel()
            return

        now = datetime.now(timezone.utc)
        next_at = respawn.get("next_respawn_at")

        if next_at and now >= next_at:
            # Mantém janela aberta visualmente durante RESPAWN_OPEN_SECONDS e depois agenda a próxima.
            await update_respawn_panel()
            await asyncio.sleep(RESPAWN_OPEN_SECONDS)

            if not milsim_state.get("active"):
                return

            respawn = milsim_state.get("respawn", {})
            if respawn.get("mission") != mission_name or not respawn.get("active"):
                return

            respawn["next_respawn_at"] = datetime.now(timezone.utc) + timedelta(seconds=RESPAWN_INTERVAL_SECONDS)
            await update_respawn_panel()
        else:
            await update_respawn_panel()
            await asyncio.sleep(10)


async def start_respawn_cycle(mission_name: str):
    if not respawn_allowed_for_mission(mission_name):
        await stop_respawn_cycle()
        return

    respawn = milsim_state.setdefault("respawn", {})

    old_task = respawn.get("task")
    if old_task and not old_task.done():
        old_task.cancel()

    now = datetime.now(timezone.utc)
    respawn["active"] = True
    respawn["mission"] = mission_name
    respawn["cycle_started_at"] = now
    respawn["next_respawn_at"] = now + timedelta(seconds=RESPAWN_INTERVAL_SECONDS)

    await update_respawn_panel()

    task = asyncio.create_task(respawn_panel_loop(mission_name))
    respawn["task"] = task


async def stop_respawn_cycle():
    respawn = milsim_state.setdefault("respawn", {})
    respawn["active"] = False

    task = respawn.get("task")
    if task and not task.done():
        task.cancel()

    await update_respawn_panel()


@bot.command()
@commands.has_permissions(administrator=True)
async def painel_respawn(ctx):
    await update_respawn_panel()
    await ctx.send("✅ Painel de respawn criado/atualizado no Comando Central.", delete_after=10)


@bot.command()
@commands.has_permissions(administrator=True)
async def respawn_now(ctx):
    respawn = milsim_state.setdefault("respawn", {})
    if not respawn.get("active"):
        return await ctx.send("⚠️ O ciclo de respawn não está ativo.", delete_after=10)

    respawn["next_respawn_at"] = datetime.now(timezone.utc)
    await update_respawn_panel()
    await ctx.send("🟢 Janela de respawn forçada.", delete_after=10)




async def send_regroup_two_minute_notice(team: str):
    color = discord.Color.blue() if team == "azul" else discord.Color.red()
    emoji = "🔵" if team == "azul" else "🔴"
    name = "TASK FORCE AZUL" if team == "azul" else "TASK FORCE VERMELHA"

    await milsim_send_to_team(
        team,
        embed=tactical_embed(
            f"⚠️ ALERTA OPERACIONAL — {emoji} {name}",
            "O período de reorganização aproxima-se do fim.\n\n"
            "Finalizem reabastecimento, confirmem comunicações e preparem mobilização imediata.",
            color,
            [
                {"name": "📍 ORDEM", "value": "▸ Reorganizar unidade\n▸ Verificar equipamento\n▸ Confirmar munições\n▸ Aguardar transmissão do comando"},
                {"name": "⏱️ NOVAS ORDENS", "value": "**Em 2 minutos**"}
            ],
            footer="COMANDO CENTRAL • REAGRUPAMENTO OPERACIONAL"
        )
    )



def milsim_is_gm(ctx):
    return any(role.id == GM_ROLE_ID for role in ctx.author.roles) or ctx.author.guild_permissions.administrator


async def broadcast_two_minute_regroup_notice():
    for t in ["azul", "vermelho"]:
        if milsim_state["teams"][t].get("phase") == "regroup":
            if not milsim_state.get("regroup_notice_sent", {}).get(t, False):
                milsim_state["regroup_notice_sent"][t] = True
                await send_regroup_two_minute_notice(t)


async def milsim_start_decryption(team: str):
    # Missão 2A — BUNKER-551 inicia a transmissão Azul no BUNKER.
    milsim_state["decryption"] = {
        "active": True,
        "cancelled": False,
        "team": team,
        "mission": "mission_2"
    }

    await milsim_log("📡 Transmissão de dados iniciada no BUNKER pela Task Force Azul. Janela de 10 minutos ativa.")

    await asyncio.sleep(MILSPEED["decryption_seconds"])

    if not milsim_state["active"]:
        return

    decryption = milsim_state.get("decryption", {})

    if not decryption.get("active"):
        return

    if decryption.get("cancelled"):
        return

    if decryption.get("team") != team:
        return

    team_state = milsim_state["teams"][team]

    if team_state["current"] != "mission_2":
        return

    # A transmissão só dá pontos e encerra a missão quando os 10 minutos terminarem.
    milsim_state["scores"][team] += 20
    milsim_state["decryption"]["active"] = False

    await milsim_log("✅ Transmissão BUNKER-551 concluída pela Task Force Azul. +20 pontos.")
    await set_both_teams_to_regroup_after_objective(team, "mission_2", "BUNKER-551")
    await update_status_panel()


@bot.command()
async def start_op(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem iniciar a operação.", delete_after=10)

    milsim_state["active"] = True
    milsim_state["timeout_resolution_active"] = False
    milsim_state["scores"] = {"azul": 0, "vermelho": 0}
    milsim_state["mission_end_times"] = {"azul": None, "vermelho": None}
    milsim_state["regroup_notice_sent"] = {"azul": False, "vermelho": False}
    milsim_state["status_panel_message_id"] = None
    milsim_state["team_status_panel_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["respawn"] = {
        "active": False,
        "mission": None,
        "cycle_started_at": None,
        "next_respawn_at": None,
        "panel_message_id": None,
        "task": None
    }
    milsim_state["respawn"] = {
        "active": False,
        "mission": None,
        "cycle_started_at": None,
        "next_respawn_at": None,
        "panel_message_id": None,
        "task": None
    }
    milsim_state["mission_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["previous_mission_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["rest_seconds"] = {"azul": 0, "vermelho": 0}
    milsim_state["rest_until"] = {"azul": None, "vermelho": None}
    milsim_state["rest_ready"] = {"azul": False, "vermelho": False}
    milsim_state["rest_warned"] = {"azul": False, "vermelho": False}
    for task in milsim_state.get("rest_tasks", {}).values():
        if task and not task.done():
            task.cancel()
    milsim_state["rest_tasks"] = {"azul": None, "vermelho": None}
    milsim_state["decryption"] = {"active": False, "cancelled": False, "team": None, "mission": None}
    milsim_state["mission3_route"] = {"vermelho_step": 0}
    milsim_state["captured_players"] = []
    milsim_state["mission_branch"] = None

    for team in ["azul", "vermelho"]:
        milsim_state["teams"][team] = {
            "current": "mission_1",
            "phase": "mission",
            "regrouped": False,
            "completed_codes": []
        }

    comando = milsim_get_channel(COMANDO_CHANNEL_ID)

    if comando:
        await comando.send(embed=tactical_embed(
            "📡 COMANDO CENTRAL — OPERAÇÃO DUALITY",
            "〔 TRANSMISSÃO GLOBAL 〕\n\nEscutem com atenção operadores.\n\nNas últimas horas foi confirmada atividade militar clandestina dentro do complexo industrial abandonado no setor norte. Reconhecimento aéreo identificou movimentações relacionadas com inteligência militar, servidores SATCOM e armazenamento de dados classificados capazes de comprometer operações em larga escala.\n\nA partir deste momento, todas as equipas entram em prontidão máxima, todas as frequências entram em modo operacional e qualquer inteligência recuperada tem prioridade absoluta.\n\nHoje não existem reforços. Não existe evacuação. E não existe segunda oportunidade.\n\nO sucesso desta operação poderá decidir o controlo total da região.\n\nPreparem equipamento. Sincronizem rádios. Confirmem munições.",
            discord.Color.dark_gold(),
            [
                {"name": "⏱️ INÍCIO DA OPERAÇÃO", "value": "**T-60 SEGUNDOS**", "inline": False}
            ]
        ))

    await asyncio.sleep(60)

    for t in ["azul", "vermelho"]:
        milsim_state["mission_end_times"][t] = datetime.now(timezone.utc) + timedelta(seconds=MISSION_TIME_LIMITS["mission_1"])

    await send_team_embed_with_status_last(
        "azul",
        tactical_embed(
            "🔵 MISSÃO 01 — SECURE DRIVES",
            "〔 TASK FORCE AZUL 〕\n\nReconhecimento de drones confirmou a existência de 5 discos rígidos escondidos no interior do complexo CQB. Apenas um contém a verdadeira inteligência operacional. Os restantes foram criados para atrasar qualquer tentativa de extração inimiga.\n\nCada corredor pode esconder uma emboscada. Cada porta pode conter contacto inimigo. O sucesso desta missão irá desbloquear acesso direto à inteligência principal da operação.",
            discord.Color.blue(),
            [
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Localizar os 5 discos rígidos\n▸ Guardar todo o material na caixa segura\n▸ Regressar ao HQ com a caixa intacta\n▸ Identificar o disco verdadeiro", "inline": False},
                {"name": "⚠️ REGRAS OPERACIONAIS", "value": "▸ O operador da caixa não pode correr\n▸ Caso seja eliminado, a caixa permanece no local\n▸ O conteúdo não pode cair nas mãos inimigas", "inline": False},
                {"name": "📡 ORDEM", "value": "Movam-se rápido. Mantenham a caixa segura. E não deixem ninguém para trás.", "inline": False}
            ]
        )
    )

    await send_team_embed_with_status_last(
        "vermelho",
        tactical_embed(
            "🔴 MISSÃO 01 — INTERCEPT PROTOCOL",
            "〔 TASK FORCE VERMELHA 〕\n\nForças Azuis iniciaram uma operação de recuperação de inteligência dentro do setor CQB. Interceptámos comunicações que confirmam a existência de uma caixa segura contendo dados altamente sensíveis.\n\nA Azul irá tentar mover-se rapidamente antes que consigamos fechar o perímetro. Não lhes deem tempo. Não lhes deem espaço. Não permitam que a inteligência saia do CQB.",
            discord.Color.red(),
            [
                {"name": "📌 OBJETIVO DA MISSÃO", "value": "▸ Localizar forças Azuis\n▸ Intercetar a caixa segura\n▸ Impedir extração inimiga\n▸ Comprometer o conteúdo da caixa", "inline": False},
                {"name": "⚠️ REGRAS OPERACIONAIS", "value": "▸ A caixa só pode ser comprometida dentro da base Vermelha\n▸ A destruição da inteligência é prioridade máxima", "inline": False},
                {"name": "📡 ORDEM", "value": "Interceção autorizada.", "inline": False}
            ]
        )
    )

    await create_team_timer_panel("azul")
    await create_team_timer_panel("vermelho")
    await create_team_status_panel("azul")
    await create_team_status_panel("vermelho")

    asyncio.create_task(mission_timer("azul", "mission_1"))
    asyncio.create_task(mission_timer("vermelho", "mission_1"))
    await update_status_panel()

    await milsim_log("🎖️ Operação DUALITY iniciada.")
    await ctx.send("✅ Operação iniciada.")



async def milsim_resolve_sabotage_by_red():
    decryption = milsim_state.get("decryption", {})

    if decryption.get("active") and decryption.get("team") == "azul":
        milsim_state["decryption"]["cancelled"] = True
        milsim_state["decryption"]["active"] = False
        await milsim_log("💥 Sabotagem vermelha cancelou a transmissão Azul no BUNKER.")
        return True

    return False


@bot.command()
async def codigo(ctx, codigo: str):
    team = milsim_team_from_channel(ctx.channel.id)

    if not team:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal da tua equipa.", delete_after=10)

    if not milsim_state["active"]:
        return await ctx.send("⚠️ A operação ainda não está ativa.")

    codigo = codigo.upper().strip()

    if codigo not in MISSION_CODES:
        return await ctx.send("❌ Código inválido ou intel comprometida.")

    data = MISSION_CODES[codigo]

    if data["team"] != team:
        return await ctx.send("❌ Este código não pertence à tua cadeia operacional.")

    if codigo == "RAVEN-119":
        decryption = milsim_state.get("decryption", {})
        if not decryption.get("active") or decryption.get("team") != "azul":
            return await ctx.send("⚠️ Não existe nenhuma desencriptação ativa para sabotar.")

    team_state = milsim_state["teams"][team]

    if codigo in team_state["completed_codes"]:
        return await ctx.send("⚠️ Este código já foi utilizado.")

    if team_state["current"] != data["mission"]:
        return await ctx.send("⚠️ Código correto, mas fora da fase operacional atual.")

    if data.get("type") == "checkpoint":
        expected_step = milsim_state["mission3_route"]["vermelho_step"] + 1
        if data.get("step") != expected_step:
            return await ctx.send(
                f"⚠️ Checkpoint fora de ordem. Próximo checkpoint esperado: `{expected_step}`."
            )

    if codigo == "EXFIL-337":
        required = data.get("requires_step", 0)
        if milsim_state["mission3_route"]["vermelho_step"] < required:
            return await ctx.send(
                "⚠️ Ainda faltam checkpoints obrigatórios antes do código final."
            )

    if codigo == "BUNKER-551":
        if team != "azul":
            return await ctx.send("❌ Apenas a Task Force Azul pode iniciar a transmissão no BUNKER.", delete_after=10)
        if milsim_state.get("decryption", {}).get("active"):
            return await ctx.send("⚠️ Já existe uma transmissão ativa no BUNKER.", delete_after=10)

        team_state["completed_codes"].append(codigo)

        await purge_team_status_panels(team)
        await prepare_team_channel_for_new_milsim_embed(team)
        msg = await ctx.send(embed=apply_medical_rules_to_embed(build_mission_embed_with_status(team, data["embed"](), "📡 Transmissão ativa")))
        milsim_state["mission_message_ids"][team] = msg.id
        await finish_team_channel_after_new_milsim_embed(team)

        enemy_alert = data.get("enemy_alert_embed")
        if enemy_alert:
            enemy = milsim_enemy(team)
            await send_team_embed_with_status_last(enemy, enemy_alert())

        asyncio.create_task(milsim_start_decryption(team))
        await milsim_log("📡 Código `BUNKER-551` validado pela Task Force Azul. Transmissão iniciada no BUNKER.")
        await update_status_panel()
        return

    if codigo == "BLACK-916":
        if team != "vermelho":
            return await ctx.send("❌ Apenas a Task Force Vermelha pode iniciar o hack SATCOM.", delete_after=10)
        if milsim_state.get("satcom", {}).get("hack_active"):
            return await ctx.send("⚠️ O hack SATCOM já está em curso.", delete_after=10)
        team_state["completed_codes"].append(codigo)
        await start_satcom_hack()
        return

    if codigo == "OMEGA-440":
        if team != "azul":
            return await ctx.send("❌ Apenas a Task Force Azul pode cancelar o hack SATCOM.", delete_after=10)
        if not milsim_state.get("satcom", {}).get("hack_active"):
            return await ctx.send("⚠️ Ainda não existe hack SATCOM ativo para cancelar.", delete_after=10)
        team_state["completed_codes"].append(codigo)
        await cancel_satcom_hack()
        return

    team_state["completed_codes"].append(codigo)

    if data.get("branch") and not milsim_state.get("mission_branch"):
        milsim_state["mission_branch"] = data["branch"]

    milsim_state["scores"][team] += data["points"]

    if data["type"] == "checkpoint":
        await purge_team_status_panels(team)
        await prepare_team_channel_for_new_milsim_embed(team)
        msg = await ctx.send(embed=apply_medical_rules_to_embed(build_mission_embed_with_status(team, data["embed"](), "✅ Checkpoint validado")))
        milsim_state["mission_message_ids"][team] = msg.id
        await finish_team_channel_after_new_milsim_embed(team)

        enemy_alert = data.get("enemy_alert_embed")
        if enemy_alert:
            enemy = milsim_enemy(team)
            await send_team_embed_with_status_last(enemy, enemy_alert())

    if codigo == "RAVEN-119":
        sabotaged = await milsim_resolve_sabotage_by_red()
        if not sabotaged:
            return await ctx.send("⚠️ Não existe transmissão ativa para sabotar.", delete_after=10)

    await milsim_log(f"🔐 Código `{codigo}` validado por **{team.upper()}**. +{data['points']} pontos.")
    await update_status_panel()

    if data["type"] == "checkpoint":
        milsim_state["mission3_route"]["vermelho_step"] = data["step"]
        await update_status_panel()
        return

    if data["type"] == "decryption":
        return

    if codigo == "CACHE-777":
        if team != "azul":
            return await ctx.send("❌ Apenas a Task Force Azul pode validar a captura da caixa.", delete_after=10)

        satcom = milsim_state.get("satcom", {})
        if not satcom.get("secondary_active"):
            return await ctx.send("⚠️ A missão secundária ainda não está ativa ou já terminou.", delete_after=10)

        satcom["secondary_active"] = False
        satcom["secondary_completed"] = True
        satcom["secondary_winner"] = "azul"
        await delete_all_secondary_timer_panels()

        task = satcom.get("secondary_task")
        if task and not task.done():
            task.cancel()

        team_state["completed_codes"].append(codigo)
        milsim_state["scores"]["azul"] += 10

        await milsim_send_to_team(
            "azul",
            embed=tactical_embed(
                "📦 SECUNDÁRIA CONCLUÍDA — CAIXA EXTRAÍDA",
                "A caixa de suprimentos Vermelha foi capturada, extraída e registada com sucesso na base Azul.",
                discord.Color.blue(),
                [
                    {"name": "🏆 Resultado", "value": "Vitória secundária Azul", "inline": False},
                    {"name": "🏆 Pontos", "value": "**+10 pontos atribuídos**", "inline": True}
                ],
                footer="COMANDO CENTRAL • RESULTADO SECUNDÁRIO"
            )
        )

        await milsim_send_to_team(
            "vermelho",
            embed=tactical_embed(
                "📦 ACAMPAMENTO COMPROMETIDO",
                "A Task Force Azul capturou a caixa de suprimentos e validou a extração na base Azul.",
                discord.Color.red(),
                [
                    {"name": "📍 Ordem", "value": "Operadores secundários regressam à base e aguardam próxima janela operacional.", "inline": False}
                ],
                footer="COMANDO CENTRAL • RESULTADO SECUNDÁRIO"
            )
        )

        await milsim_log("📦 CACHE-777 validado pela Azul. Missão secundária vencida pela Azul. +10 pontos.")
        await update_status_panel()
        return

    if data["type"] == "secondary":
        await milsim_log(f"📦 Objetivo secundário `{codigo}` validado por **{team.upper()}**.")
        await update_status_panel()
        return

    if data["type"] == "end":
        await purge_team_status_panels(team)
        await prepare_team_channel_for_new_milsim_embed(team)
        msg = await ctx.send(embed=apply_medical_rules_to_embed(build_mission_embed_with_status(team, data["embed"](), "🏁 Operação terminada")))
        milsim_state["mission_message_ids"][team] = msg.id
        milsim_state["active"] = False
        await cleanup_team_timer_panels(team)
        await milsim_log("🏁 Operação terminada por código final.")
        return

    await set_both_teams_to_regroup_after_objective(team, team_state["current"], codigo)


async def set_both_teams_to_regroup_after_objective(winning_team: str, mission_name: str, codigo: str = None):
    enemy = milsim_enemy(winning_team)

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "regroup"
        milsim_state["teams"][t]["regrouped"] = False

    await stop_respawn_cycle()

    winner_color = discord.Color.blue() if winning_team == "azul" else discord.Color.red()
    enemy_color = discord.Color.blue() if enemy == "azul" else discord.Color.red()

    result_map = {
        "SHADOW-214": {
            "winner_title": "DISCO VERDADEIRO CONFIRMADO",
            "winner_reason": "A caixa segura regressou ao HQ e o disco verdadeiro foi identificado. A inteligência principal permanece intacta.",
            "enemy_title": "INTEL AZUL EXTRAÍDA",
            "enemy_reason": "A Task Force Azul conseguiu retirar a caixa segura e identificar o disco verdadeiro."
        },
        "VIPER-771": {
            "winner_title": "CAIXA COMPROMETIDA",
            "winner_reason": "A caixa segura foi capturada e comprometida na base Vermelha. A inteligência principal da Azul deixou de ser confiável.",
            "enemy_title": "CAIXA SEGURA COMPROMETIDA",
            "enemy_reason": "A Task Force Vermelha conseguiu capturar a caixa e comprometer o conteúdo."
        },
        "BUNKER-551": {
            "winner_title": "TRANSMISSÃO BUNKER CONCLUÍDA",
            "winner_reason": "A Task Force Azul manteve o controlo do BUNKER até ao fim da sequência e concluiu o envio de dados para a central.",
            "enemy_title": "TRANSMISSÃO AZUL CONCLUÍDA",
            "enemy_reason": "A Task Force Azul completou o envio de dados no BUNKER antes de a transmissão ser sabotada."
        },
        "RAVEN-119": {
            "winner_title": "TRANSMISSÃO SABOTADA",
            "winner_reason": "A transmissão de dados foi interrompida com sucesso antes de chegar à central.",
            "enemy_title": "TRANSMISSÃO INTERROMPIDA",
            "enemy_reason": "A Task Force Vermelha sabotou o uplink e interrompeu o envio de dados."
        },
        "FRAGMENT-404": {
            "winner_title": "FRAGMENTOS RECUPERADOS",
            "winner_reason": "Os fragmentos foram reunidos e parte da inteligência foi restaurada com sucesso.",
            "enemy_title": "RECUPERAÇÃO AZUL CONFIRMADA",
            "enemy_reason": "A Task Force Azul conseguiu reconstruir os fragmentos de backup."
        },
        "HIJACK-515": {
            "winner_title": "CONVOY INTERCEPTADO",
            "winner_reason": "A carga inimiga foi capturada pela Task Force Azul e retirada para o HQ com sucesso.",
            "enemy_title": "CONVOY PERDIDO",
            "enemy_reason": "A Task Force Azul intercetou o transporte e capturou a carga operacional."
        },
        "GHOST-802": {
            "winner_title": "CARGA DEPOSITADA",
            "winner_reason": "A carga inimiga foi intercetada e depositada com sucesso no ponto indicado.",
            "enemy_title": "CARGA INTERCETADA",
            "enemy_reason": "A Task Force Azul conseguiu capturar e depositar a carga operacional."
        },
        "EXFIL-337": {
            "winner_title": "CONVOY VALIDADO",
            "winner_reason": "A carga completou a rota obrigatória e foi extraída com sucesso após os checkpoints.",
            "enemy_title": "CONVOY INIMIGO CONCLUÍDO",
            "enemy_reason": "A Task Force Vermelha completou a rota de transporte e concluiu a extração final."
        },
        "BLACK-916": {
            "winner_title": "SATCOM HACK INICIADO",
            "winner_reason": "A estação SATCOM foi ativada e o hack às comunicações inimigas entrou em curso.",
            "enemy_title": "INTERFERÊNCIA SATCOM DETETADA",
            "enemy_reason": "A Task Force Vermelha iniciou atividade hostil numa estação SATCOM clandestina."
        },
        "OMEGA-440": {
            "winner_title": "HACK SATCOM CANCELADO",
            "winner_reason": "O terminal SATCOM foi localizado e a sequência de hackeamento foi cancelada.",
            "enemy_title": "HACK SATCOM INTERROMPIDO",
            "enemy_reason": "A Task Force Azul localizou o terminal e cancelou a operação SATCOM."
        },
        "CACHE-777": {
            "winner_title": "SUPPLY CACHE RECUPERADA",
            "winner_reason": "A supply crate foi recuperada e extraída com sucesso durante a emboscada.",
            "enemy_title": "ACAMPAMENTO COMPROMETIDO",
            "enemy_reason": "A Task Force Azul conseguiu infiltrar o acampamento e recuperar recursos."
        },
        "NOVA-999": {
            "winner_title": "TRANSMISSÃO FINAL CONCLUÍDA",
            "winner_reason": "A transmissão final foi concluída e a Task Force Azul assumiu vantagem decisiva.",
            "enemy_title": "VITÓRIA AZUL CONFIRMADA",
            "enemy_reason": "A transmissão final foi concluída pela Task Force Azul."
        },
        "IRON-666": {
            "winner_title": "TRANSMISSÃO FINAL IMPEDIDA",
            "winner_reason": "A transmissão inimiga foi interrompida e a Task Force Vermelha assumiu vantagem decisiva.",
            "enemy_title": "VITÓRIA VERMELHA CONFIRMADA",
            "enemy_reason": "A transmissão final foi impedida pela Task Force Vermelha."
        },
    }

    result = result_map.get(codigo or "", {})

    # SATCOM codes do NOT trigger regroup/retreat flow.
    if codigo == "BLACK-916":
        if winning_team == "vermelho":
            await milsim_log("📡 SATCOM hack iniciado pela Task Force Vermelha.")
        return

    if codigo == "OMEGA-440":
        await milsim_send_to_team(
            "azul",
            embed=tactical_embed(
                "📡 HACK CANCELADO",
                "O terminal SATCOM foi localizado e a sequência de hackeamento foi interrompida com sucesso.",
                discord.Color.blue(),
                footer="COMANDO CENTRAL • SATCOM SECURED"
            )
        )

        await milsim_send_to_team(
            "vermelho",
            embed=tactical_embed(
                "📡 HACK SATCOM INTERROMPIDO",
                "A Task Force Azul conseguiu localizar o terminal e cancelar a operação SATCOM.",
                discord.Color.red(),
                footer="COMANDO CENTRAL • SATCOM FAILURE"
            )
        )

        await milsim_log("📡 SATCOM cancelado pela Task Force Azul.")
        return

    winner_title = result.get("winner_title", "OBJETIVO CONCLUÍDO")
    winner_reason = result.get("winner_reason", "O objetivo foi concluído com sucesso.")
    enemy_title = result.get("enemy_title", "OBJETIVO INIMIGO CONFIRMADO")
    enemy_reason = result.get("enemy_reason", "O objetivo inimigo foi confirmado e a janela operacional atual foi encerrada.")

    mission_label = mission_display_name(mission_name)

    await send_team_embed_plain(
        winning_team,
        tactical_embed(
            f"✅ Ordem de Retirada - Missão Bem sucedida ({mission_label})",
            "📡 A missão foi um êxito! Regressem de imediato à base, reorganizem a equipa, reabasteçam equipamento, confirmem comunicações e preparem-se para a próxima janela de missão.\n\n"
            "📌 **Motivo**\n"
            f"{winner_title}\n{winner_reason}\n\n"
            "Estejam em alerta e aguardem novas ordens!",
            winner_color,
            footer="COMANDO CENTRAL • ORDEM DE RETIRADA"
        )
    )

    await send_team_embed_plain(
        enemy,
        tactical_embed(
            f"⚠️ Ordem de Retirada - Missão Fracassada! ({mission_label})",
            "📡 Regressem de imediato ao HQ, reorganizem a equipa e preparem-se para novas ordens.\n\n"
            "📌 **Motivo**\n"
            f"{enemy_title}\n{enemy_reason}\n\n"
            "Estejam em alerta e aguardem novas ordens!",
            enemy_color,
            footer="COMANDO CENTRAL • ORDEM DE RETIRADA"
        )
    )

    await milsim_log(
        f"📡 Objetivo concluído por **{winning_team.upper()}**. "
        f"**{enemy.upper()}** recebeu ordem de retirada. Reagrupamento ativo até ao fim da janela operacional."
    )

    await update_status_panel()


async def advance_milsim_phase(old_mission: str):
    if old_mission == "mission_4" and not mission4_all_objectives_finished():
        await warn_mission4_not_finished()
        return

    if old_mission not in NEXT_MISSIONS:
        return

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "mission"
        milsim_state["teams"][t]["regrouped"] = False
        milsim_state["regroup_notice_sent"][t] = False

        if old_mission == "mission_4":
            milsim_state["teams"][t]["current"] = "final"
        else:
            next_number = int(old_mission.split("_")[1]) + 1
            milsim_state["teams"][t]["current"] = f"mission_{next_number}"

        if milsim_state["teams"][t]["current"] == "mission_3":
            milsim_state["mission3_route"]["vermelho_step"] = 0

        await force_archive_current_mission_embed(t, "Nova fase operacional iniciada.")
        await purge_team_status_panels(t)

        if old_mission == "mission_3":
            if not milsim_state.get("satcom", {}).get("selected", {}).get("azul"):
                select_satcom_operators()
            if t == "vermelho":
                await milsim_send_to_team(t, embed=build_satcom_red_initial_embed())
            else:
                await milsim_send_to_team(t, embed=build_satcom_interference_embed())

        elif old_mission == "mission_1" and milsim_state.get("mission_branch") == "compromised":
            alt_mission = globals().get("NEXT_MISSIONS_ALT", {}).get("mission_1_compromised", {})
            if t in alt_mission:
                await milsim_send_to_team(t, embed=alt_mission[t]())
            else:
                await milsim_send_to_team(t, embed=NEXT_MISSIONS[old_mission][t]())
        else:
            await milsim_send_to_team(t, embed=NEXT_MISSIONS[old_mission][t]())

        await create_team_status_panel(t)

    for t in ["azul", "vermelho"]:
        asyncio.create_task(mission_timer(t, milsim_state["teams"][t]["current"]))

    await milsim_log("📡 Nova fase operacional transmitida automaticamente às duas equipas.")
    await update_status_panel()





@bot.command(name="end_op")
@commands.has_permissions(administrator=True)
async def end_op(ctx):
    if not milsim_state.get("active"):
        return await ctx.send("⚠️ Não existe nenhuma operação ativa.", delete_after=10)

    milsim_state["active"] = False

    # Cancelar task de respawn
    respawn = milsim_state.get("respawn", {})
    task = respawn.get("task")
    if task and not task.done():
        task.cancel()

    respawn["active"] = False

    # Colocar equipas em estado final
    for team in ["azul", "vermelho"]:
        milsim_state["teams"][team]["phase"] = "ended"

    azul_score = milsim_state["scores"]["azul"]
    vermelho_score = milsim_state["scores"]["vermelho"]

    if azul_score > vermelho_score:
        resultado = "🔵 TASK FORCE AZUL VENCEU A OPERAÇÃO"
        cor = discord.Color.blue()
    elif vermelho_score > azul_score:
        resultado = "🔴 TASK FORCE VERMELHA VENCEU A OPERAÇÃO"
        cor = discord.Color.red()
    else:
        resultado = "⚖️ OPERAÇÃO TERMINOU EMPATADA"
        cor = discord.Color.orange()

    embed = tactical_embed(
        "🏁 OPERAÇÃO TERMINADA",
        "O COMANDO CENTRAL declarou encerrada a operação atual.\n\n"
        "Todas as unidades devem regressar ao HQ e iniciar procedimento de desmobilização.",
        cor,
        [
            {"name": "📌 Resultado Final", "value": resultado, "inline": False},
            {"name": "🔵 Score Azul", "value": f"**{azul_score} pts**", "inline": True},
            {"name": "🔴 Score Vermelho", "value": f"**{vermelho_score} pts**", "inline": True},
            {"name": "📍 Ordem Final", "value": "Desmobilizar equipamento, confirmar material e aguardar debrief operacional.", "inline": False}
        ],
        footer="COMANDO CENTRAL • FIM DE OPERAÇÃO"
    )

    # Enviar para equipas
    for team in ["azul", "vermelho"]:
        await send_team_embed_with_status_last(team, embed.copy())

    # Enviar para comando central
    comando = milsim_get_channel(COMANDO_CHANNEL_ID)
    if comando:
        await comando.send(embed=embed)

    await ctx.send("✅ Operação terminada.", delete_after=10)


@bot.command()
@commands.has_permissions(administrator=True)
async def limpardados(ctx):
    milsim_state["scores"] = {"azul": 0, "vermelho": 0}
    milsim_state["active"] = False
    milsim_state["timeout_resolution_active"] = False
    milsim_state["mission_end_times"] = {"azul": None, "vermelho": None}
    milsim_state["regroup_notice_sent"] = {"azul": False, "vermelho": False}
    milsim_state["mission_branch"] = None
    milsim_state["mission3_route"] = {"vermelho_step": 0}
    milsim_state["captured_players"] = []
    milsim_state["decryption"] = {"active": False, "cancelled": False, "team": None, "mission": None}
    milsim_state["mission_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["previous_mission_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["team_status_panel_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["team_timer_panel_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["secondary_timer_panel_message_ids"] = {"azul": None, "vermelho": None}
    milsim_state["respawn"] = {
        "active": False,
        "mission": None,
        "cycle_started_at": None,
        "next_respawn_at": None,
        "panel_message_id": None,
        "task": None
    }
    milsim_state["respawn"] = {
        "active": False,
        "mission": None,
        "cycle_started_at": None,
        "next_respawn_at": None,
        "panel_message_id": None,
        "task": None
    }
    milsim_state["teams"] = {
        "azul": {"current": "mission_1", "phase": "mission", "regrouped": False, "completed_codes": []},
        "vermelho": {"current": "mission_1", "phase": "mission", "regrouped": False, "completed_codes": []}
    }
    await ctx.send("✅ Dados MILSIM limpos com sucesso.")


@bot.command()
async def reagrupado(ctx):
    team = milsim_team_from_channel(ctx.channel.id)

    if not team:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal da tua equipa.")

    if not milsim_state["active"]:
        return await ctx.send("⚠️ A operação não está ativa.")

    team_state = milsim_state["teams"][team]

    if team_state["phase"] != "regroup":
        return await ctx.send("⚠️ A tua equipa ainda não recebeu ordem de reagrupamento.")

    team_state["regrouped"] = True

    remaining = format_time_remaining(team)

    await purge_team_status_panels(team)
    await ctx.send(embed=tactical_embed(
        "✅ REAGRUPAMENTO CONFIRMADO",
        "A unidade regressou ao HQ e iniciou reorganização operacional.\n\n"
        "Reabasteçam equipamento, confirmem comunicações e aguardem nova transmissão do comando.",
        discord.Color.green(),
        [
            {"name": "📍 ESTADO", "value": "🔵 REAGRUPAMENTO OPERACIONAL"},
            {"name": "⏱️ NOVAS ORDENS EM", "value": f"**{remaining}**"}
        ],
        footer="COMANDO CENTRAL • REAGRUPAMENTO"
    ))
    await create_team_status_panel(team)

    await milsim_log(f"✅ **{team.upper()}** confirmou chegada à base. Reagrupamento ativo até ao fim da janela da missão.")
    await update_status_panel()



@bot.command()
@commands.has_permissions(administrator=True)
async def codigos_operadores(ctx):
    embed = tactical_embed(
        "🪪 CÓDIGOS DE OPERADOR",
        "Lista interna de códigos usados para capturas.",
        discord.Color.dark_gold(),
        [
            {"name": "🔵 Task Force Azul", "value": "\n".join([f"`{code}` — {name}" for code, name in AZUL_OPERATOR_CODES.items()]), "inline": False},
            {"name": "🔴 Task Force Vermelha", "value": "\n".join([f"`{code}` — {name}" for code, name in VERMELHO_OPERATOR_CODES.items()]), "inline": False},
            {"name": "📌 Uso", "value": "`!capturar CODIGO-OPERADOR`", "inline": False}
        ],
        footer="COMANDO CENTRAL • CÓDIGOS DE OPERADOR"
    )
    await ctx.send(embed=embed)


@bot.command()
async def capturar(ctx, codigo_operador: str):
    team = milsim_team_from_channel(ctx.channel.id)

    if not team:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal da tua equipa.", delete_after=10)

    if not milsim_state["active"]:
        return await ctx.send("⚠️ A operação não está ativa.", delete_after=10)

    codigo_operador = codigo_operador.upper().strip()

    if codigo_operador not in ALL_OPERATOR_CODES:
        return await ctx.send("❌ Código de operador inválido.", delete_after=10)

    enemy_codes = VERMELHO_OPERATOR_CODES if team == "azul" else AZUL_OPERATOR_CODES
    own_codes = AZUL_OPERATOR_CODES if team == "azul" else VERMELHO_OPERATOR_CODES

    if codigo_operador in own_codes:
        return await ctx.send("❌ Esse operador pertence à tua própria equipa.", delete_after=10)

    if codigo_operador not in enemy_codes:
        return await ctx.send("❌ Esse operador não pertence à equipa inimiga.", delete_after=10)

    if codigo_operador in milsim_state.get("captured_players", []):
        return await ctx.send("⚠️ Esse operador já foi capturado anteriormente.", delete_after=10)

    milsim_state.setdefault("captured_players", []).append(codigo_operador)
    milsim_state["scores"][team] += 5

    operator_name = ALL_OPERATOR_CODES[codigo_operador]
    enemy = milsim_enemy(team)

    await send_team_embed_with_status_last(
        team,
        tactical_embed(
            "🪪 OPERADOR CAPTURADO",
            f"Operador inimigo confirmado: **{operator_name}**\\nCódigo: `{codigo_operador}`",
            discord.Color.dark_red(),
            [
                {
                    "name": "📡 INTEL RECUPERADA",
                    "value": "▸ Identificação operacional confirmada\\n▸ Operador removido temporariamente do terreno\\n▸ Deve regressar à base e aguardar próxima janela de respawn",
                    "inline": False
                },
                {"name": "🏆 Pontos", "value": "**+5 pontos atribuídos**", "inline": True}
            ],
            footer="COMANDO CENTRAL • CAPTURA DE OPERADOR"
        )
    )

    await milsim_send_to_team(
        enemy,
        embed=tactical_embed(
            "⚠️ OPERADOR COMPROMETIDO",
            f"Um operador da vossa equipa foi capturado.\\n\\nCódigo comprometido: `{codigo_operador}`",
            discord.Color.orange(),
            [
                {"name": "📍 ORDEM", "value": "O operador capturado deve regressar à base e aguardar próxima janela de respawn.", "inline": False}
            ],
            footer="COMANDO CENTRAL • ALERTA DE CAPTURA"
        )
    )

    await milsim_log(f"🪪 **{team.upper()}** capturou `{codigo_operador}` ({operator_name}). +5 pontos.")
    await update_status_panel()


@bot.command()
async def opstatus(ctx):
    if ctx.channel.id not in [AZUL_CHANNEL_ID, VERMELHO_CHANNEL_ID, GM_CHANNEL_ID, COMANDO_CHANNEL_ID]:
        return await ctx.send("⚠️ Comando disponível apenas em canais operacionais.", delete_after=10)

    await ctx.send(embed=build_operation_status_embed())


@bot.command()
async def painel_op(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    comando = milsim_get_channel(COMANDO_CHANNEL_ID)

    if not comando:
        return await ctx.send("⚠️ Canal de comando não encontrado.", delete_after=10)

    msg = await comando.send(embed=build_operation_status_embed())
    milsim_state["status_panel_message_id"] = msg.id

    await ctx.send("✅ Painel operacional criado no canal de comando.")


@bot.command()
async def score(ctx):
    await ctx.send(embed=tactical_embed(
        "🏆 SCORE OPERACIONAL",
        f"🔵 Azul: **{milsim_state['scores']['azul']} pts**\n🔴 Vermelho: **{milsim_state['scores']['vermelho']} pts**",
        discord.Color.dark_gold()
    ))


@bot.command()
async def gm_blackout(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    embed = tactical_embed(
        "⚫ ALERTA GLOBAL — BLACKOUT",
        "FALHA GENERALIZADA DE COMUNICAÇÕES",
        discord.Color.dark_grey(),
        [
            {"name": "CONDIÇÕES NO TERRENO", "value": "▸ Lanternas proibidas\n▸ Comunicações limitadas\n▸ Apenas squad leaders autorizados em rádio\n▸ Duração estimada: 10 minutos"},
            {"name": "📍 ORDEM", "value": "Mantenham eficácia operacional."}
        ]
    )

    comando = milsim_get_channel(COMANDO_CHANNEL_ID)
    if comando:
        await comando.send(embed=embed)

    await send_team_embed_with_status_last("azul", embed)
    await send_team_embed_with_status_last("vermelho", embed)
    await milsim_log("⚫ Blackout ativado pelo Game Master.")
    await ctx.send("✅ Blackout enviado.")



@bot.command()
async def skip_pausa(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    if not milsim_state.get("active"):
        return await ctx.send("⚠️ A operação não está ativa.", delete_after=10)

    phases = {t: milsim_state["teams"][t].get("phase") for t in ["azul", "vermelho"]}

    if not any(phase == "regroup" for phase in phases.values()):
        return await ctx.send("⚠️ Não existe pausa/reagrupamento ativo para avançar.", delete_after=10)

    mission_name = milsim_state["teams"]["azul"].get("current") or milsim_state["teams"]["vermelho"].get("current")

    for t in ["azul", "vermelho"]:
        if milsim_state["teams"][t].get("phase") == "regroup":
            milsim_state["mission_end_times"][t] = datetime.now(timezone.utc)
            milsim_state["regroup_notice_sent"][t] = True

    await milsim_log("⏭️ Pausa/reagrupamento avançado manualmente pelo Game Master.")

    await ctx.send("⏭️ Pausa/reagrupamento avançado. A próxima fase será iniciada agora.", delete_after=10)

    if mission_name:
        await advance_milsim_phase(mission_name)


@bot.command(name="passarpausa")
async def passar_pausa(ctx):
    await skip_pausa(ctx)



@tasks.loop(minutes=10)
async def limpar_timers_auto():
    if not milsim_state.get("active"):
        return

    await cleanup_team_timer_panels("azul")
    await cleanup_team_timer_panels("vermelho")
    await cleanup_secondary_timer_panels("azul")
    await cleanup_secondary_timer_panels("vermelho")
    await create_team_timer_panel("azul")
    await create_team_timer_panel("vermelho")


@bot.command()
async def limpar_timers(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    await cleanup_team_timer_panels("azul")
    await cleanup_team_timer_panels("vermelho")

    # Recriar apenas se houver operação ativa.
    if milsim_state.get("active"):
        await create_team_timer_panel("azul")
        await create_team_timer_panel("vermelho")

    await ctx.send("✅ Timers operacionais limpos/recriados.", delete_after=10)


@bot.command()
async def gm_next(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    old_mission = milsim_state["teams"]["azul"]["current"]

    if old_mission not in NEXT_MISSIONS:
        return await ctx.send("⚠️ Não existe próxima fase configurada.")

    if old_mission == "mission_4" and not mission4_all_objectives_finished():
        await warn_mission4_not_finished(ctx)
        return

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "mission"
        milsim_state["teams"][t]["regrouped"] = False

        if old_mission == "mission_4":
            milsim_state["teams"][t]["current"] = "final"
        else:
            next_number = int(old_mission.split("_")[1]) + 1
            milsim_state["teams"][t]["current"] = f"mission_{next_number}"

        milsim_state["mission_end_times"][t] = datetime.now(timezone.utc) + timedelta(
            seconds=MISSION_TIME_LIMITS[milsim_state["teams"][t]["current"]]
        )

        await send_team_embed_with_status_last(t, NEXT_MISSIONS[old_mission][t]())

    for t in ["azul", "vermelho"]:
        asyncio.create_task(mission_timer(t, milsim_state["teams"][t]["current"]))

    await milsim_log("⏭️ Game Master forçou próxima fase operacional.")
    await ctx.send("✅ Próxima fase enviada.")


@bot.command()
async def gm_end(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    milsim_state["active"] = False

    azul = milsim_state["scores"]["azul"]
    vermelho = milsim_state["scores"]["vermelho"]

    if azul > vermelho:
        vencedor = "🔵 TASK FORCE AZUL"
    elif vermelho > azul:
        vencedor = "🔴 TASK FORCE VERMELHA"
    else:
        vencedor = "EMPATE OPERACIONAL"

    embed = tactical_embed(
        "🏁 FIM DA OPERAÇÃO",
        f"🔵 Azul: **{azul} pts**\n🔴 Vermelho: **{vermelho} pts**\n\nVENCEDOR:\n**{vencedor}**",
        discord.Color.dark_gold()
    )

    comando = milsim_get_channel(COMANDO_CHANNEL_ID)
    if comando:
        await comando.send(embed=embed)

    await milsim_log("🏁 Operação terminada manualmente.")
    await update_status_panel()
    await ctx.send("✅ Operação terminada.")



# =========================
# 🧹 LIMPAR CHAT
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def limparchat(ctx, quantidade: int = 100):
    try:
        await ctx.channel.purge(limit=quantidade + 1)

        aviso = await ctx.send(
            f"🧹 Canal limpo com sucesso. ({quantidade} mensagens removidas)"
        )

        await asyncio.sleep(3)
        await aviso.delete()

    except discord.Forbidden:
        await ctx.send("❌ Não tenho permissões para apagar mensagens.", delete_after=10)

    except Exception as e:
        await ctx.send(f"⚠️ Erro ao limpar chat: {e}", delete_after=10)


# ---------- START ----------
bot.run(TOKEN)
