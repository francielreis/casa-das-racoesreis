import os
import secrets
import hmac
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

from flask import (
    Flask, request, redirect, session, flash,
    render_template_string, Response, send_from_directory, url_for
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from markupsafe import Markup


app = Flask(__name__)
app.secret_key = os.getenv(
    "SECRET_KEY",
    "casa-das-racoes-chave-temporaria"
)

database_url = os.getenv(
    "DATABASE_URL",
    "sqlite:///casa_racoes.db"
)

if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Cookies de sessao seguros no Render (HTTPS), inclusive durante recebimentos.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("RENDER", "").lower() == "true"


db = SQLAlchemy(app)
FUSO_LOJA = ZoneInfo("America/Fortaleza")


def agora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def data_local(data):
    return data.replace(
        tzinfo=timezone.utc
    ).astimezone(FUSO_LOJA)


app.jinja_env.filters["data_local"] = data_local


class Produto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    categoria = db.Column(db.String(100), default="")
    unidade = db.Column(db.String(30), default="un")
    custo = db.Column(db.Numeric(12, 2), default=0)
    preco = db.Column(db.Numeric(12, 2), default=0)
    estoque = db.Column(db.Numeric(12, 3), default=0)
    estoque_minimo = db.Column(db.Numeric(12, 3), default=0)

    @property
    def lucro_unitario(self):
        return Decimal(self.preco or 0) - Decimal(self.custo or 0)

    @property
    def margem(self):
        custo = Decimal(self.custo or 0)
        if custo <= 0:
            return 0
        margem = self.lucro_unitario / custo * 100
        return float(margem)


class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    telefone = db.Column(db.String(50), default="")


class EntradaEstoque(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produto.id"),
        nullable=False
    )
    quantidade = db.Column(db.Numeric(12, 3), nullable=False)
    custo_unitario = db.Column(db.Numeric(12, 2), nullable=False)
    data = db.Column(db.DateTime, default=agora_utc)
    produto = db.relationship("Produto")


class Venda(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(
        db.Integer,
        db.ForeignKey("produto.id"),
        nullable=False
    )
    cliente_id = db.Column(
        db.Integer,
        db.ForeignKey("cliente.id"),
        nullable=True
    )
    quantidade = db.Column(db.Numeric(12, 3), nullable=False)
    preco_unitario = db.Column(db.Numeric(12, 2), nullable=False)
    custo_unitario = db.Column(db.Numeric(12, 2), nullable=False)
    total = db.Column(db.Numeric(12, 2), nullable=False)
    lucro = db.Column(db.Numeric(12, 2), nullable=False)
    tipo = db.Column(db.String(20), default="avista")
    pago = db.Column(db.Numeric(12, 2), default=0)
    data = db.Column(db.DateTime, default=agora_utc)

    produto = db.relationship("Produto")
    cliente = db.relationship("Cliente")

    @property
    def saldo_devedor(self):
        saldo = Decimal(self.total or 0) - Decimal(self.pago or 0)
        if saldo < 0:
            return Decimal("0")
        return saldo


class VendaGrupo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(
        db.Integer,
        db.ForeignKey("cliente.id"),
        nullable=True
    )
    tipo = db.Column(db.String(20), nullable=False, default="avista")
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    pago = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    data = db.Column(db.DateTime, nullable=False, default=agora_utc)

    cliente = db.relationship("Cliente")


class VendaGrupoItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grupo_id = db.Column(
        db.Integer,
        db.ForeignKey("venda_grupo.id"),
        nullable=False,
        index=True
    )
    venda_id = db.Column(
        db.Integer,
        db.ForeignKey("venda.id"),
        nullable=False,
        unique=True,
        index=True
    )

    grupo = db.relationship("VendaGrupo")
    venda = db.relationship("Venda")


class VendaRequisicao(db.Model):
    # Chave unica contra repeticao do formulario, inclusive com varios workers.
    chave = db.Column(db.String(100), primary_key=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("venda_grupo.id"), nullable=False)
    criado_em = db.Column(db.DateTime, nullable=False, default=agora_utc)


class Caixa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    data = db.Column(db.DateTime, default=agora_utc)


class ExclusaoVenda(db.Model):
    venda_id = db.Column(db.Integer, db.ForeignKey("venda.id"), primary_key=True)
    data = db.Column(db.DateTime, nullable=False, default=agora_utc)


def vendas_validas():
    return Venda.query.filter(~db.exists().where(ExclusaoVenda.venda_id == Venda.id))


class FechamentoCaixa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ultima_venda_id = db.Column(db.Integer, nullable=False, default=0)
    ultimo_movimento_id = db.Column(db.Integer, nullable=False, default=0)
    data = db.Column(db.DateTime, nullable=False, default=agora_utc)


def vendas_atuais():
    limite = db.session.query(
        db.func.max(FechamentoCaixa.ultima_venda_id)
    ).scalar() or 0
    return vendas_validas().filter(Venda.id > limite)


def movimentos_atuais():
    limite = db.session.query(
        db.func.max(FechamentoCaixa.ultimo_movimento_id)
    ).scalar() or 0
    return Caixa.query.filter(Caixa.id > limite)


def eh_compra_de_mercadoria(movimento):
    # Compras de estoque pagas pelo caixa, separadas das despesas da loja.
    return (
        movimento.tipo == "saida"
        and (movimento.descricao or "").startswith("Compra de mercadoria - ")
    )


CATEGORIAS_DESPESAS = (
    "Aluguel", "Energia elétrica", "Água", "Internet", "Telefone",
    "Frete", "Manutenção", "Funcionários", "Impostos e taxas", "Outros"
)
PREFIXO_DESPESA = "Despesa da loja - "


def eh_despesa_da_loja(movimento):
    return (
        movimento.tipo == "saida"
        and (movimento.descricao or "").startswith(PREFIXO_DESPESA)
    )


class BannerLoja(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    imagem = db.Column(db.LargeBinary, nullable=False)
    mime = db.Column(db.String(30), nullable=False)
    slogan = db.Column(db.String(180), nullable=False)
    ordem = db.Column(db.Integer, default=0, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)


def numero(valor):
    try:
        return Decimal(str(valor).replace(",", "."))
    except Exception:
        return Decimal("0")


def dinheiro(valor):
    valor = Decimal(valor or 0)
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


app.jinja_env.filters["dinheiro"] = dinheiro


CENTAVO = Decimal("0.01")


def dinheiro_decimal(valor):
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def valor_monetario_valido(valor, permite_zero=False):
    return (valor.is_finite()
            and (valor >= 0 if permite_zero else valor > 0)
            and valor <= Decimal("9999999999.99")
            and valor == valor.quantize(CENTAVO))


def quantidade_legivel(valor):
    """Exibe 1 em vez de 1.000, preservando frações como 0,5."""
    texto = format(Decimal(valor or 0), "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")


app.jinja_env.filters["quantidade_legivel"] = quantidade_legivel


def logado():
    return session.get("logado") is True

def csrf_token():
    """Gera um token imprevisivel vinculado a sessao autenticada."""
    token = session.get("_csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def exigir_csrf_em_alteracoes():
    """Rejeita requisicoes POST sem token valido antes de qualquer gravacao."""
    if request.method != "POST":
        return None
    esperado = session.get("_csrf_token")
    recebido = request.form.get("_csrf_token", "")
    if (not isinstance(esperado, str)
            or not isinstance(recebido, str)
            or len(esperado) < 32
            or len(recebido) > 256
            or not hmac.compare_digest(recebido, esperado)):
        app.logger.warning("POST bloqueado por CSRF ausente/invalido: %s", request.path)
        return ("Verificação de segurança expirada ou inválida. "
                "Atualize o formulário antes de tentar novamente.", 400)
    return None


@app.after_request
def proteger_respostas_administrativas(resposta):
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if session.get("logado"):
        resposta.headers["Cache-Control"] = "private, no-store"
    return resposta




BASE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Casa das Rações Reis</title>
<style>
*{box-sizing:border-box}
body{
    margin:0;
    background:#f3f5f4;
    font-family:Arial,sans-serif;
    color:#222
}
header{background:#176b3a;color:white;padding:18px}
header h2{margin:0}
nav{
    background:white;
    padding:8px;
    overflow-x:auto;
    white-space:nowrap;
    border-bottom:1px solid #ddd
}
nav a{
    display:inline-block;
    text-decoration:none;
    color:#176b3a;
    font-weight:bold;
    padding:11px
}
.container{max-width:1100px;margin:auto;padding:15px}

/* Pesquisa em todas as telas administrativas, inclusive mobile */
.pesquisa-global{
    display:flex;flex-wrap:wrap;align-items:stretch;gap:8px;
    padding:12px 15px;background:#e8f1eb;border-bottom:1px solid #cbded1
}
.pesquisa-global input,.pesquisa-global select{
    margin:0;background:#fff;min-height:44px;font-size:15px
}
.pesquisa-global input{flex:2 1 210px;min-width:0}
.pesquisa-global select{flex:1 1 130px;max-width:230px;min-width:120px}
.pesquisa-global button{min-height:44px;flex:0 0 auto}
.pesquisa-local{
    display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0 9px
}
.pesquisa-local label{color:#4d5f53;font-size:13px;font-weight:bold}
.pesquisa-local input{margin:0;width:min(100%,350px);min-height:40px;background:#fff}
.pesquisa-local small{color:#58665c}
.pesquisa-resultado{background:white;border:1px solid #dfe8e2;border-radius:10px;padding:13px;margin:9px 0}
.pesquisa-resultado a{color:#176b3a;font-weight:bold}
tr:target{background:#fff4c5!important;outline:2px solid #e0a619}
.pesquisa-resultado small{color:#536158;display:block;margin-top:5px}
@media(max-width:550px){
    .pesquisa-global{gap:7px;padding:10px}
    .pesquisa-global input{flex:1 1 100%;width:100%}
    .pesquisa-global select{flex:1 1 auto;max-width:none}
    .pesquisa-global button{padding:10px 13px}
}

.cards{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
    gap:12px
}
.card{
    background:white;
    padding:18px;
    border-radius:10px;
    box-shadow:0 2px 7px #00000015
}
.card h3{font-size:14px;color:#666;margin-top:0}
.valor{color:#176b3a;font-weight:bold;font-size:23px}
.valor.despesa{color:#b42318}
.formulario{
    max-width:650px;
    background:white;
    padding:18px;
    border-radius:10px
}
input,select{
    width:100%;
    padding:11px;
    margin:5px 0 13px;
    border:1px solid #bbb;
    border-radius:6px
}
button{
    background:#176b3a;
    color:white;
    padding:12px 18px;
    border:none;
    border-radius:7px;
    font-weight:bold;
    cursor:pointer
}
table{width:100%;background:white;border-collapse:collapse}
th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}
th{background:#e9eceb}
.sucesso{
    background:#d1e7dd;
    padding:12px;
    border-radius:6px;
    margin-bottom:12px
}
.erro{
    background:#f8d7da;
    padding:12px;
    border-radius:6px;
    margin-bottom:12px
}
h1{color:#176b3a}
.tabela{overflow-x:auto}
.aviso{background:#fff3cd;padding:12px;border-radius:6px}
/* Compras agrupadas: leitura confortável em telas pequenas. */
.compras-ajuda{color:#5a645f;margin:-4px 0 16px;line-height:1.5}
.lista-compras{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.compra-card{background:#fff;border:1px solid #e0e6e2;border-radius:14px;overflow:hidden;box-shadow:0 2px 8px #0000000b}
.compra-topo{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:16px}
.compra-cliente{font-size:18px;font-weight:700;overflow-wrap:anywhere;margin:6px 0}
.compra-data{font-size:13px;color:#606a63}
.compra-numero{color:#176b3a;font-size:12px;font-weight:700}
.compra-situacao{background:#edf5ef;color:#176b3a;padding:7px 9px;border-radius:8px;font-weight:700;font-size:12px;white-space:nowrap}
.compra-situacao.fiado{background:#fff2dc;color:#8a4e0b}
.compra-itens{list-style:none;padding:0 16px;margin:0}
.compra-item{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid #edf0ed}
.compra-item-nome{font-weight:700;display:block;overflow-wrap:anywhere}
.compra-item-dados{font-size:12px;color:#636c65;display:block;line-height:1.6;margin-top:3px}
.compra-item-total{font-weight:700;text-align:right;white-space:nowrap}
.compra-rodape{padding:13px 16px 16px;background:#f7f9f7;border-top:1px solid #e6ece7}
.compra-totais{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.compra-totais small{color:#606a63;display:block;margin-bottom:5px}
.compra-totais strong{font-size:18px;display:block;white-space:nowrap}
.compra-totais .lucro{color:#176b3a}
.compra-ver{display:block;text-align:center;color:#176b3a;text-decoration:none;border:1px solid #98b8a3;background:#fff;font-weight:700;border-radius:8px;padding:11px;margin-top:14px}
.compra-ver:focus-visible{outline:3px solid #176b3a;outline-offset:2px}
.compra-vazia{background:#fff;border-radius:12px;padding:20px}
@media(max-width:540px){
 .lista-compras{grid-template-columns:1fr}
 .compra-card{border-radius:11px}
 .compra-totais strong{font-size:16px}
}
</style>
</head>
<body>
<header>
<h2>🌾 Casa das Rações Reis</h2>
<small>Estoque • Caixa • Vendas • Fiado • Lucro</small>
</header>

{% if session.get("logado") %}
<form class="pesquisa-global" action="{{ url_for('pesquisa') }}" method="get" role="search" aria-label="Pesquisar no sistema">
<label for="busca-global" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)">Pesquisar em todas as telas</label>
<input id="busca-global" type="search" name="q" maxlength="120" autocomplete="off"
 placeholder="🔎 Pesquisar cliente, produto, venda, fiado..."
 value="{{ request.args.get('q', '')[:120] if request.path == '/pesquisa' else '' }}" required>
<label for="pesquisa-secao" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)">Onde pesquisar</label>
<select id="pesquisa-secao" name="secao" aria-label="Onde pesquisar">
{% for chave, titulo in [
('tudo', 'Todas as seções'), ('produtos', 'Produtos'), ('clientes', 'Clientes'),
('vendas', 'Vendas'), ('fiados', 'Fiados'), ('caixa', 'Caixa'), ('entradas', 'Entradas')
] %}
<option value="{{ chave }}" {% if request.path == '/pesquisa' and request.args.get('secao','tudo') == chave %}selected{% endif %}>{{ titulo }}</option>
{% endfor %}
</select>
<button type="submit">Pesquisar</button>
</form>
<nav>
<a href="/">Painel</a>
<a href="/pesquisa">🔎 Pesquisa</a>
<a href="/produtos">Produtos</a>
<a href="/entrada">Entrada</a>
<a href="/venda">Venda</a>
<a href="/vendas">Vendas / Excluir</a>
<a href="/clientes">Clientes</a>
<a href="/fiado">Fiado</a>
<a href="/caixa">Caixa</a>
<a href="/despesas">Despesas</a>
<a href="/relatorios">Relatórios</a>
<a href="/analise-negocio">Análise do Negócio</a>
<a href="/auditoria-financeira">Auditoria financeira</a>
<a href="/catalogo">Catálogo público</a>
<a href="/banners">Banners</a>
<a href="/logout">Sair</a>
</nav>
{% endif %}

<div class="container">
{% with mensagens = get_flashed_messages(with_categories=true) %}
{% for categoria, mensagem in mensagens %}
<div class="{{ categoria }}">{{ mensagem }}</div>
{% endfor %}
{% endwith %}
{{ conteudo }}
</div>
{% if session.get("logado") %}
<script>
(function () {
    // Pesquisa local somente nas linhas presentes na tabela. A pesquisa geral
    // acima permite buscar registros fora da página, inclusive históricos.
    function normalizar(valor) {
        return (valor || "").normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "").toLowerCase();
    }
    if (typeof document.querySelectorAll !== "function") return;
    document.querySelectorAll(".tabela table").forEach(function (tabela, indice) {
        const linhas = Array.from(tabela.querySelectorAll("tbody tr")).filter(
            function (linha) {return linha.querySelector("td") !== null;}
        );
        if (linhas.length < 2) return;
        const caixa = tabela.closest(".tabela");
        if (!caixa || caixa.dataset.pesquisaLocal) return;
        caixa.dataset.pesquisaLocal = "sim";
        const barra = document.createElement("div");
        barra.className = "pesquisa-local";
        const label = document.createElement("label");
        const campo = document.createElement("input");
        campo.type = "search";
        campo.id = "pesquisa-tabela-" + indice;
        campo.placeholder = "Filtrar nesta tabela...";
        campo.setAttribute("aria-label", "Filtrar linhas visíveis nesta tabela");
        label.htmlFor = campo.id;
        label.textContent = "🔎 Buscar na lista";
        const status = document.createElement("small");
        status.setAttribute("aria-live", "polite");
        status.textContent = linhas.length + " linha(s) visíveis";
        barra.append(label, campo, status);
        caixa.parentNode.insertBefore(barra, caixa);
        campo.addEventListener("input", function () {
            const termo = normalizar(campo.value.trim());
            let total = 0;
            linhas.forEach(function (linha) {
                const encontrou = !termo || normalizar(linha.textContent).includes(termo);
                linha.hidden = !encontrou;
                if (encontrou) total++;
            });
            status.textContent = total + " de " + linhas.length + " linha(s) visíveis"
                + (termo ? " nesta página" : "");
        });
    });
})();
</script>
{% endif %}
</body>
</html>
"""


def pagina(template, **dados):
    conteudo = render_template_string(template, **dados)
    return render_template_string(
        BASE,
        conteudo=Markup(conteudo)
    )


@app.route("/pesquisa")
def pesquisa():
    """Pesquisa autenticada em todo o histórico, sem alterar registros."""
    if not logado():
        return redirect("/login")

    termo = request.args.get("q", "").strip()[:120]
    secao = request.args.get("secao", "tudo")
    secoes_permitidas = {"tudo", "produtos", "clientes", "vendas", "fiados", "caixa", "entradas"}
    if secao not in secoes_permitidas:
        secao = "tudo"

    resultados = {categoria: [] for categoria in (
        "produtos", "clientes", "vendas", "fiados", "caixa", "entradas"
    )}
    if termo:
        # Escapa os curingas do LIKE para pesquisar o texto literalmente.
        termo_escapado = (termo.replace("\\", "\\\\")
                         .replace("%", "\\%").replace("_", "\\_"))
        padrao = "%" + termo_escapado + "%"
        numero_id = termo.lstrip("#")
        id_busca = int(numero_id) if numero_id.isdecimal() and len(numero_id) <= 9 else None
        def filtros(*colunas, identificador=None):
            expressoes = [coluna.ilike(padrao, escape="\\") for coluna in colunas]
            if id_busca is not None and identificador is not None:
                expressoes.append(identificador == id_busca)
            return or_(*expressoes)

        if secao in ("tudo", "produtos"):
            resultados["produtos"] = (Produto.query.filter(
                filtros(Produto.nome, Produto.categoria, identificador=Produto.id)
            ).order_by(Produto.nome, Produto.id).limit(30).all())

        if secao in ("tudo", "clientes"):
            resultados["clientes"] = (Cliente.query.filter(
                filtros(Cliente.nome, Cliente.telefone, identificador=Cliente.id)
            ).order_by(Cliente.nome, Cliente.id).limit(30).all())

        if secao in ("tudo", "vendas", "fiados"):
            busca_vendas = (vendas_validas()
                           .join(Produto, Venda.produto_id == Produto.id)
                           .outerjoin(Cliente, Venda.cliente_id == Cliente.id)
                           .filter(filtros(
                               Produto.nome, Cliente.nome,
                               identificador=Venda.id
                           )))
            if secao in ("tudo", "vendas"):
                resultados["vendas"] = (busca_vendas.order_by(
                    Venda.id.desc()).limit(30).all())
            if secao in ("tudo", "fiados"):
                resultados["fiados"] = (busca_vendas.filter(
                    Venda.tipo == "fiado").order_by(Venda.id.desc()).limit(30).all())

        if secao in ("tudo", "caixa"):
            resultados["caixa"] = Caixa.query.filter(
                filtros(Caixa.descricao, Caixa.tipo, identificador=Caixa.id)
            ).order_by(Caixa.id.desc()).limit(30).all()

        if secao in ("tudo", "entradas"):
            resultados["entradas"] = (EntradaEstoque.query.join(
                Produto, EntradaEstoque.produto_id == Produto.id
            ).filter(filtros(
                Produto.nome, identificador=EntradaEstoque.id
            )).order_by(EntradaEstoque.id.desc()).limit(30).all())

    total_encontrados = sum(len(itens) for itens in resultados.values())
    return pagina("""
<h1>🔎 Pesquisa no sistema</h1>
<p>Pesquise pelo nome do cliente, nome do produto, telefone ou número da
venda. Consulte também fiados, compras de estoque e movimentos do caixa,
inclusive anteriores ao último zeramento.</p>
{% if not termo %}
<p class="aviso">Digite um termo na barra de pesquisa acima e selecione a seção.</p>
{% else %}
<p>Resultado da busca por <strong>{{ termo }}</strong>:
{{ total_encontrados }} registro(s) exibido(s).
A consulta mostra até 30 resultados por seção. Use o filtro de seção
para refinar a pesquisa.</p>
{% if not total_encontrados %}
<p class="aviso">Nenhum registro encontrado. Confira o nome ou o número informado.</p>
{% endif %}

{% if secao in ("tudo", "produtos") and resultados.produtos %}
<h2>Produtos ({{ resultados.produtos|length }})</h2>
{% for produto in resultados.produtos %}
<div class="pesquisa-resultado">
<a href="{{ url_for('produtos') }}#produto-{{ produto.id }}">{{ produto.nome }}</a>
<small>Categoria: {{ produto.categoria or 'Sem categoria' }} ·
Compra: {{ produto.custo|dinheiro }} · Venda: {{ produto.preco|dinheiro }} ·
Estoque: {{ produto.estoque|quantidade_legivel }} {{ produto.unidade }}</small>
</div>
{% endfor %}
{% endif %}

{% if secao in ("tudo", "clientes") and resultados.clientes %}
<h2>Clientes ({{ resultados.clientes|length }})</h2>
{% for cliente in resultados.clientes %}
<div class="pesquisa-resultado">
<a href="{{ url_for('clientes') }}#cliente-{{ cliente.id }}">{{ cliente.nome }}</a>
<small>Cliente #{{ cliente.id }} · Telefone: {{ cliente.telefone or 'Não informado' }}</small>
</div>
{% endfor %}
{% endif %}

{% if secao in ("tudo", "vendas") and resultados.vendas %}
<h2>Vendas ({{ resultados.vendas|length }})</h2>
{% for venda in resultados.vendas %}
<div class="pesquisa-resultado">
<a href="{{ url_for('detalhe_venda', venda_id=venda.id) }}">Venda #{{ venda.id }}</a>
— {{ venda.cliente.nome if venda.cliente else 'Sem cliente' }} ·
{{ venda.produto.nome }}
<small>{{ (venda.data|data_local).strftime('%d/%m/%Y %H:%M') }} ·
{{ venda.total|dinheiro }} · {{ 'Fiado' if venda.tipo == 'fiado' else 'À vista' }}</small>
</div>
{% endfor %}
{% endif %}

{% if secao in ("tudo", "fiados") and resultados.fiados %}
<h2>Fiados ({{ resultados.fiados|length }})</h2>
{% for venda in resultados.fiados %}
<div class="pesquisa-resultado">
<a href="{{ url_for('detalhe_venda', venda_id=venda.id) }}">Fiado #{{ venda.id }}</a>
— {{ venda.cliente.nome if venda.cliente else 'Sem cliente' }} ·
{{ venda.produto.nome }}
<small>Total: {{ venda.total|dinheiro }} · Pago: {{ venda.pago|dinheiro }} ·
Pendente: {{ venda.saldo_devedor|dinheiro }}
{% if venda.saldo_devedor == 0 %}· Quitado{% endif %}</small>
</div>
{% endfor %}
{% endif %}

{% if secao in ("tudo", "caixa") and resultados.caixa %}
<h2>Caixa ({{ resultados.caixa|length }})</h2>
{% for movimento in resultados.caixa %}
<div class="pesquisa-resultado">
<strong>{{ movimento.descricao }}</strong>
<small>#{{ movimento.id }} ·
{{ (movimento.data|data_local).strftime('%d/%m/%Y %H:%M') }} ·
{{ 'Entrada' if movimento.tipo == 'entrada' else 'Saída' }} ·
{{ movimento.valor|dinheiro }}</small>
</div>
{% endfor %}
{% endif %}

{% if secao in ("tudo", "entradas") and resultados.entradas %}
<h2>Entradas de mercadoria ({{ resultados.entradas|length }})</h2>
{% for item in resultados.entradas %}
<div class="pesquisa-resultado">
<strong>{{ item.produto.nome }}</strong>
<small>Entrada #{{ item.id }} ·
{{ (item.data|data_local).strftime('%d/%m/%Y %H:%M') }} ·
Quantidade: {{ item.quantidade|quantidade_legivel }} {{ item.produto.unidade }} ·
Custo na compra: {{ item.custo_unitario|dinheiro }}</small>
</div>
{% endfor %}
{% endif %}
{% endif %}
<p><a href="/">Voltar ao painel</a></p>
""", termo=termo, secao=secao, resultados=resultados,
       total_encontrados=total_encontrados)


@app.route("/vendas/<int:venda_id>")
def detalhe_venda(venda_id):
    """Consulta read-only para localizar vendas antigas pela pesquisa."""
    if not logado():
        return redirect("/login")
    venda = vendas_validas().filter(Venda.id == venda_id).first_or_404()
    return pagina("""
<h1>Venda #{{ venda.id }}</h1>
<div class="pesquisa-resultado">
<p><strong>Cliente:</strong> {{ venda.cliente.nome if venda.cliente else 'Sem cliente' }}</p>
<p><strong>Data:</strong> {{ (venda.data|data_local).strftime('%d/%m/%Y %H:%M') }}</p>
<p><strong>Produto:</strong> {{ venda.produto.nome }}</p>
<p><strong>Quantidade:</strong> {{ venda.quantidade|quantidade_legivel }} {{ venda.produto.unidade }}</p>
<p><strong>Preço unitário:</strong> {{ venda.preco_unitario|dinheiro }}</p>
<p><strong>Total da venda:</strong> {{ venda.total|dinheiro }}</p>
<p><strong>Lucro bruto registrado:</strong> {{ venda.lucro|dinheiro }}</p>
<p><strong>Tipo:</strong> {{ 'Fiado' if venda.tipo == 'fiado' else 'À vista' }}</p>
<p><strong>Pago:</strong> {{ venda.pago|dinheiro }}</p>
{% if venda.tipo == 'fiado' %}
<p><strong>A receber:</strong> {{ venda.saldo_devedor|dinheiro }}</p>
{% endif %}
</div>
<p><a href="/pesquisa">Nova pesquisa</a> · <a href="/vendas">Ver vendas</a></p>
""", venda=venda)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario_correto = os.getenv("ADMIN_USER", "admin")
        senha_correta = os.getenv("ADMIN_PASSWORD", "admin123")

        usuario = request.form["usuario"]
        senha = request.form["senha"]

        if usuario == usuario_correto and senha == senha_correta:
            session.clear()
            session["logado"] = True
            csrf_token()
            return redirect("/")

        flash("Usuário ou senha incorretos.", "erro")

    return pagina("""
<div class="formulario">
<h1>Entrar</h1>
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<label>Usuário</label>
<input name="usuario" required>
<label>Senha</label>
<input type="password" name="senha" required>
<button>Entrar</button>
</form>
<p><a href="/catalogo">Ver catálogo da loja</a></p>
</div>
""")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def painel():
    if not logado():
        return catalogo_publico()

    vendas = vendas_atuais().all()
    total_vendido = sum(
        (Decimal(v.total or 0) for v in vendas),
        Decimal("0")
    )
    lucro_total = sum(
        (Decimal(v.lucro or 0) for v in vendas),
        Decimal("0")
    )
    fiado = sum(
        (v.saldo_devedor for v in vendas_validas().filter_by(tipo="fiado").all()),
        Decimal("0")
    )

    movimentos = movimentos_atuais().all()
    entradas = sum(
        (Decimal(m.valor) for m in movimentos if m.tipo == "entrada"),
        Decimal("0")
    )
    saidas = sum(
        (Decimal(m.valor) for m in movimentos if m.tipo == "saida"),
        Decimal("0")
    )
    saldo = entradas - saidas
    compras_registradas = [
        m for m in movimentos if eh_compra_de_mercadoria(m)
    ]
    total_compras = sum(
        (Decimal(m.valor or 0) for m in compras_registradas),
        Decimal("0")
    )
    despesas_da_loja = sum(
        (Decimal(m.valor or 0) for m in movimentos if eh_despesa_da_loja(m)),
        Decimal("0")
    )
    outras_saidas = saidas - total_compras - despesas_da_loja
    ultimas_compras = sorted(
        compras_registradas, key=lambda m: m.id, reverse=True
    )[:10]

    baixos = Produto.query.filter(
        Produto.estoque <= Produto.estoque_minimo
    ).all()
    # Reune os itens pela compra original. Vendas antigas sem grupo
    # continuam aparecendo individualmente, sem inferir ligacoes por horario.
    ultimas_por_chave = {}
    linhas_agrupadas = (
        vendas_atuais()
        .outerjoin(VendaGrupoItem, VendaGrupoItem.venda_id == Venda.id)
        .with_entities(Venda, VendaGrupoItem.grupo_id)
        .order_by(Venda.id.desc())
        .all()
    )
    for venda, grupo_id in linhas_agrupadas:
        chave = ("compra", grupo_id) if grupo_id is not None else ("venda", venda.id)
        if chave not in ultimas_por_chave:
            ultimas_por_chave[chave] = {
                "data": venda.data,
                "cliente": venda.cliente.nome if venda.cliente else "Sem cliente",
                "grupo_id": grupo_id,
                "tipo": venda.tipo,
                "itens": [],
                "total": Decimal("0"),
                "lucro": Decimal("0"),
            }
        resumo = ultimas_por_chave[chave]
        resumo["itens"].append(venda)
        resumo["total"] += Decimal(venda.total or 0)
        resumo["lucro"] += Decimal(venda.lucro or 0)
    ultimas = list(ultimas_por_chave.values())[:10]
    for resumo in ultimas:
        resumo["itens"].reverse()

    return pagina("""
<h1>Painel</h1>
<p>Caixa, vendas e lucro desde o último zeramento.
<a href="/zerar-caixa">Zerar caixa e vendas</a></p>
<div class="cards">
<div class="card">
<h3>💵 Dinheiro em caixa</h3>
<div class="valor">{{ saldo|dinheiro }}</div>
</div>
<div class="card">
<h3>🛒 Total vendido</h3>
<div class="valor">{{ total_vendido|dinheiro }}</div>
</div>
<div class="card">
<h3>📈 Lucro bruto</h3>
<div class="valor">{{ lucro_total|dinheiro }}</div>
</div>
<div class="card">
<h3>📝 Fiado a receber</h3>
<div class="valor">{{ fiado|dinheiro }}</div>
</div>
<div class="card">
<h3>📦 Compras pagas pelo caixa</h3>
<div class="valor despesa">{{ total_compras|dinheiro }}</div>
</div>
<div class="card">
<h3>🏠 Despesas da loja</h3>
<div class="valor despesa">{{ despesas_da_loja|dinheiro }}</div>
<p><a href="/despesas">Registrar despesa</a></p>
</div>
<div class="card">
<h3>🧾 Outras saídas / retiradas</h3>
<div class="valor despesa">{{ outras_saidas|dinheiro }}</div>
</div>
<div class="card">
<h3>💸 Total de saídas do caixa</h3>
<div class="valor despesa">{{ saidas|dinheiro }}</div>
</div>
</div>

<h2>Compras pagas pelo caixa</h2>
<p>Registros desde o último zeramento. Para incluir uma compra, marque
“Registrar compra como saída do caixa” na <a href="/entrada">Entrada de mercadoria</a>.
</p>
<div class="tabela">
<table>
<tr><th>Data e horário</th><th>Mercadoria</th><th>Valor pago</th></tr>
{% for movimento in ultimas_compras %}
<tr>
<td>{{ (movimento.data|data_local).strftime("%d/%m/%Y %H:%M") }}</td>
<td>{{ movimento.descricao.replace("Compra de mercadoria - ", "", 1) }}</td>
<td>{{ movimento.valor|dinheiro }}</td>
</tr>
{% else %}
<tr><td colspan="3">Nenhuma compra paga pelo caixa neste período.</td></tr>
{% endfor %}
</table>
</div>
<p><a href="/caixa">Ver movimentações do caixa</a></p>

<h2>Estoque baixo</h2>
<div class="tabela">
<table>
<tr><th>Produto</th><th>Estoque</th></tr>
{% for produto in baixos %}
<tr id="produto-{{ produto.id }}">
<td>{{ produto.nome }}</td>
<td>{{ produto.estoque }} {{ produto.unidade }}</td>
</tr>
{% endfor %}
</table>
</div>

<h2>Últimas compras</h2>
<p class="compras-ajuda">Cada compra aparece uma vez, com todos os itens realmente registrados no sistema. Toque em "Ver detalhes" para conferir.</p>
<div class="lista-compras">
{% for compra in ultimas %}
<article class="compra-card">
<div class="compra-topo">
<div>
<span class="compra-numero">{% if compra.grupo_id is not none %}COMPRA #{{ compra.grupo_id }}{% else %}VENDA AVULSA{% endif %} · {{ compra.itens|length }} item(ns)</span>
<div class="compra-cliente">{{ compra.cliente }}</div>
<time class="compra-data">{{ (compra.data|data_local).strftime("%d/%m/%Y às %H:%M") }}</time>
</div>
<span class="compra-situacao {{ 'fiado' if compra.tipo == 'fiado' else '' }}">{{ "Fiado" if compra.tipo == "fiado" else "À vista" }}</span>
</div>
<ul class="compra-itens">
{% for item in compra.itens %}
<li class="compra-item">
<div>
<span class="compra-item-nome">{{ item.produto.nome }}</span>
<span class="compra-item-dados">{{ item.quantidade|quantidade_legivel }} {{ item.produto.unidade }} × {{ item.preco_unitario|dinheiro }}</span>
</div>
<span class="compra-item-total">{{ item.total|dinheiro }}</span>
</li>
{% endfor %}
</ul>
<div class="compra-rodape">
<div class="compra-totais">
<div><small>Total da compra</small><strong>{{ compra.total|dinheiro }}</strong></div>
<div><small>Lucro bruto</small><strong class="lucro">{{ compra.lucro|dinheiro }}</strong></div>
</div>
{% if compra.grupo_id is not none %}
<a class="compra-ver" href="{{ url_for('detalhe_compra', grupo_id=compra.grupo_id) }}">Ver detalhes da compra</a>
{% else %}
<a class="compra-ver" href="{{ url_for('listar_vendas') }}">Consultar vendas antigas</a>
{% endif %}
</div>
</article>
{% else %}
<div class="compra-vazia">Nenhuma venda registrada neste caixa. <a href="/venda">Registrar uma venda</a>.</div>
{% endfor %}
</div>
""",
        saldo=saldo,
        total_vendido=total_vendido,
        lucro_total=lucro_total,
        fiado=fiado,
        baixos=baixos,
        ultimas=ultimas,
        total_compras=total_compras,
        despesas_da_loja=despesas_da_loja,
        outras_saidas=outras_saidas,
        saidas=saidas,
        ultimas_compras=ultimas_compras
    )


@app.route("/produtos", methods=["GET", "POST"])
def produtos():
    if not logado():
        return redirect("/login")

    if request.method == "POST":
        custo = numero(request.form.get("custo", ""))
        preco = numero(request.form.get("preco", ""))
        estoque = numero(request.form.get("estoque", "0"))
        minimo = numero(request.form.get("estoque_minimo", "0"))
        if not all((
            valor_monetario_valido(custo, permite_zero=True),
            valor_monetario_valido(preco, permite_zero=True),
            estoque.is_finite() and 0 <= estoque <= 999999999,
            minimo.is_finite() and 0 <= minimo <= 999999999
        )):
            flash("Confira preços, custos e estoque: não são aceitos valores negativos ou inválidos.", "erro")
            return redirect("/produtos")
        produto = Produto(
            nome=request.form["nome"],
            categoria=request.form.get("categoria", ""),
            unidade=request.form["unidade"],
            custo=custo,
            preco=preco,
            estoque=estoque,
            estoque_minimo=minimo
        )
        db.session.add(produto)
        db.session.commit()
        flash("Produto cadastrado com sucesso.", "sucesso")
        return redirect("/produtos")

    lista = Produto.query.order_by(Produto.nome).all()
    vendas = vendas_atuais().all()
    lucros_por_produto = {}

    for venda in vendas:
        produto_id = venda.produto_id
        if produto_id not in lucros_por_produto:
            lucros_por_produto[produto_id] = Decimal("0")
        lucros_por_produto[produto_id] += Decimal(venda.lucro or 0)

    return pagina("""
<h1>Produtos</h1>
<div class="formulario">
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<label>Nome do produto</label>
<input name="nome" placeholder="Ex: Milho 60 kg" required>
<label>Categoria</label>
<input name="categoria" placeholder="Ex: Ração, milho, suplemento">
<label>Unidade</label>
<select name="unidade">
<option value="saco">Saco</option>
<option value="kg">Kg</option>
<option value="un">Unidade</option>
</select>
<label>Preço de compra</label>
<input name="custo" type="number" step="0.01" required>
<label>Preço de venda</label>
<input name="preco" type="number" step="0.01" required>
<label>Estoque inicial</label>
<input name="estoque" type="number" step="0.001" value="0">
<label>Estoque mínimo</label>
<input name="estoque_minimo" type="number" step="0.001" value="2">
<button>Cadastrar produto</button>
</form>
</div>

<h2>Mercadorias</h2>
<div class="tabela">
<table>
<tr>
<th>Produto</th>
<th>Compra</th>
<th>Venda</th>
<th>Lucro por unidade</th>
<th>Margem</th>
<th>Lucro desde o último zeramento</th>
<th>Estoque</th>
<th>Ações</th>
</tr>
{% for produto in produtos %}
<tr>
<td>{{ produto.nome }}</td>
<td>{{ produto.custo|dinheiro }}</td>
<td>{{ produto.preco|dinheiro }}</td>
<td>{{ produto.lucro_unitario|dinheiro }}</td>
<td>{{ "%.2f"|format(produto.margem) }}%</td>
<td>{{ lucros.get(produto.id, 0)|dinheiro }}</td>
<td>{{ produto.estoque }} {{ produto.unidade }}</td>
<td><a href="{{ url_for('alterar_preco', produto_id=produto.id) }}">Alterar preço</a></td>
</tr>
{% endfor %}
</table>
</div>
""", produtos=lista, lucros=lucros_por_produto)


@app.route("/produtos/<int:produto_id>/preco", methods=["GET", "POST"])
def alterar_preco(produto_id):
    if not logado():
        return redirect("/login")

    produto = db.get_or_404(Produto, produto_id)
    if "csrf_precos" not in session:
        session["csrf_precos"] = secrets.token_urlsafe(32)

    custo_texto = str(produto.custo)
    preco_texto = str(produto.preco)

    if request.method == "POST":
        recebido = request.form.get("csrf_precos", "")
        if not hmac.compare_digest(recebido, session["csrf_precos"]):
            flash("A sessão do formulário expirou. Tente novamente.", "erro")
            return redirect(request.path)

        custo_texto = request.form.get("custo", "").strip()
        preco_texto = request.form.get("preco", "").strip()
        try:
            valores = []
            for texto in (custo_texto, preco_texto):
                valor = Decimal(texto.replace(",", "."))
                if not valor.is_finite() or not 0 <= valor <= Decimal("9999999999.99"):
                    raise ValueError()
                if valor != valor.quantize(Decimal("0.01")):
                    raise ValueError()
                valores.append(valor)
        except (InvalidOperation, ValueError):
            flash("Informe preços válidos, sem valores negativos e com até duas casas decimais.", "erro")
        else:
            produto.custo, produto.preco = valores
            db.session.commit()
            flash("Preços atualizados com sucesso.", "sucesso")
            return redirect("/produtos")

    return pagina("""
<div class="formulario">
<h1>Alterar preço</h1>
<h2>{{ produto.nome }}</h2>
<p>Valores por {{ produto.unidade }}. Os novos preços serão usados nas próximas vendas.</p>
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="csrf_precos" value="{{ token }}">
<label for="custo">Preço de compra (R$)</label>
<input id="custo" name="custo" inputmode="decimal" value="{{ custo }}" required>
<label for="preco">Preço de venda (R$)</label>
<input id="preco" name="preco" inputmode="decimal" value="{{ preco }}" required>
<button type="submit">Salvar preços</button>
<a href="/produtos" style="display:inline-block;padding:12px">Cancelar</a>
</form>
</div>
""", produto=produto, custo=custo_texto, preco=preco_texto,
        token=session["csrf_precos"])


@app.route("/entrada", methods=["GET", "POST"])
def entrada():
    if not logado():
        return redirect("/login")

    produtos = Produto.query.order_by(Produto.nome).all()

    if request.method == "POST":
        produto = db.session.get(
            Produto,
            int(request.form["produto"])
        )
        quantidade = numero(request.form["quantidade"])
        custo = numero(request.form["custo"])

        if not produto:
            flash("Produto não encontrado.", "erro")
            return redirect("/entrada")

        if not quantidade.is_finite() or quantidade <= 0 or quantidade > 999999999:
            flash("Quantidade inválida.", "erro")
            return redirect("/entrada")
        if not valor_monetario_valido(custo):
            flash("Custo inválido: use valor positivo com até 2 casas decimais.", "erro")
            return redirect("/entrada")
        if quantidade != quantidade.quantize(Decimal("0.001")):
            flash("Quantidade aceita até 3 casas decimais.", "erro")
            return redirect("/entrada")

        produto = (Produto.query.filter_by(id=produto.id)
                   .with_for_update().populate_existing().first())
        estoque_antigo = Decimal(produto.estoque or 0)
        custo_antigo = Decimal(produto.custo or 0)
        if estoque_antigo < 0:
            flash("Estoque negativo. Confira antes da nova entrada.", "erro")
            return redirect("/entrada")
        novo_estoque = estoque_antigo + quantidade
        # Custo medio ponderado apenas para o estoque futuro.
        produto.custo = dinheiro_decimal(
            ((estoque_antigo * custo_antigo) + (quantidade * custo)) / novo_estoque
        )
        produto.estoque = novo_estoque

        registro = EntradaEstoque(
            produto_id=produto.id,
            quantidade=quantidade,
            custo_unitario=custo
        )
        db.session.add(registro)

        if request.form.get("registrar_caixa") == "sim":
            valor_total = dinheiro_decimal(quantidade * custo)
            db.session.add(Caixa(
                tipo="saida",
                descricao="Compra de mercadoria - " + produto.nome,
                valor=valor_total
            ))

        db.session.commit()
        flash("Entrada de mercadoria registrada.", "sucesso")
        return redirect("/entrada")

    return pagina("""
<h1>Entrada de mercadoria</h1>
<div class="formulario">
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<label>Produto</label>
<select name="produto">
{% for produto in produtos %}
<option value="{{ produto.id }}">{{ produto.nome }}</option>
{% endfor %}
</select>
<label>Quantidade</label>
<input name="quantidade" type="number" step="0.001" required>
<label>Custo por unidade ou saco</label>
<input name="custo" type="number" step="0.01" required>
<label>
<input style="width:auto" type="checkbox"
       name="registrar_caixa" value="sim">
Registrar compra como saída do caixa
</label>
<p style="font-size:13px;color:#555">Marque somente se a compra foi paga com dinheiro do caixa.
O valor aparecerá no resumo de compras do painel.</p>
<br><br>
<button>Registrar entrada</button>
</form>
</div>
""", produtos=produtos)


@app.route("/clientes", methods=["GET", "POST"])
def clientes():
    if not logado():
        return redirect("/login")

    if request.method == "POST":
        cliente = Cliente(
            nome=request.form["nome"],
            telefone=request.form.get("telefone", "")
        )
        db.session.add(cliente)
        db.session.commit()
        flash("Cliente cadastrado.", "sucesso")
        return redirect("/clientes")

    lista = Cliente.query.order_by(Cliente.nome).all()

    return pagina("""
<h1>Clientes</h1>
<div class="formulario">
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<label>Nome</label>
<input name="nome" required>
<label>Telefone</label>
<input name="telefone">
<button>Cadastrar cliente</button>
</form>
</div>

<h2>Clientes cadastrados</h2>
<div class="tabela">
<table>
<tr><th>Nome</th><th>Telefone</th></tr>
{% for cliente in clientes %}
<tr id="cliente-{{ cliente.id }}">
<td>{{ cliente.nome }}</td>
<td>{{ cliente.telefone }}</td>
</tr>
{% endfor %}
</table>
</div>
""", clientes=lista)


@app.route("/venda", methods=["GET", "POST"])
def venda():
    if not logado():
        return redirect("/login")

    produtos = Produto.query.order_by(Produto.nome).all()
    clientes = Cliente.query.order_by(Cliente.nome).all()

    if request.method == "POST":
        chave_requisicao = request.form.get("chave_requisicao", "").strip()
        if not chave_requisicao or len(chave_requisicao) > 100:
            flash("Formulário inválido. Abra a tela de venda novamente.", "erro")
            return redirect("/venda")
        pedido_existente = db.session.get(VendaRequisicao, chave_requisicao)
        if pedido_existente:
            flash("Esta compra já está registrada. Nenhuma duplicação foi feita.", "sucesso")
            return redirect(url_for("detalhe_compra", grupo_id=pedido_existente.grupo_id))

        produto_ids = request.form.getlist("produto")
        quantidades_texto = request.form.getlist("quantidade")
        tipo = request.form.get("tipo", "")

        if (
            not produto_ids
            or len(produto_ids) != len(quantidades_texto)
        ):
            flash("Adicione pelo menos um produto à compra.", "erro")
            return redirect("/venda")

        if tipo not in ("avista", "fiado"):
            flash("Escolha à vista ou fiado.", "erro")
            return redirect("/venda")

        # Junta linhas repetidas do mesmo produto.
        quantidades_por_produto = {}
        try:
            for produto_id_texto, quantidade_texto in zip(
                produto_ids, quantidades_texto
            ):
                produto_id = int(produto_id_texto)
                quantidade = numero(quantidade_texto)

                if (not quantidade.is_finite() or quantidade <= 0
                        or quantidade > 999999999
                        or quantidade != quantidade.quantize(Decimal("0.001"))):
                    raise ValueError("Quantidade inválida (máximo 3 casas decimais).")

                quantidades_por_produto[produto_id] = (
                    quantidades_por_produto.get(
                        produto_id, Decimal("0")
                    ) + quantidade
                )
        except (ValueError, TypeError):
            flash("Confira os produtos e quantidades da compra.", "erro")
            return redirect("/venda")

        cliente_id = request.form.get("cliente", "").strip()
        novo_nome = request.form.get("novo_cliente_nome", "").strip()
        novo_telefone = request.form.get(
            "novo_cliente_telefone", ""
        ).strip()

        if cliente_id and (novo_nome or novo_telefone):
            flash(
                "Selecione um cliente cadastrado ou informe um novo cliente.",
                "erro"
            )
            return redirect("/venda")

        if len(novo_nome) > 150 or len(novo_telefone) > 50:
            flash("Nome ou telefone do cliente muito longo.", "erro")
            return redirect("/venda")

        if novo_telefone and not novo_nome:
            flash("Informe o nome do novo cliente.", "erro")
            return redirect("/venda")

        if cliente_id:
            try:
                cliente_id = int(cliente_id)
            except ValueError:
                flash("Cliente inválido.", "erro")
                return redirect("/venda")

            if not db.session.get(Cliente, cliente_id):
                flash("Cliente não encontrado.", "erro")
                return redirect("/venda")
        else:
            cliente_id = None

        if tipo == "fiado" and not cliente_id and not novo_nome:
            flash(
                "Selecione um cliente ou cadastre um novo para vender fiado.",
                "erro"
            )
            return redirect("/venda")

        try:
            itens = []
            total_geral = Decimal("0")

            # Bloqueia os produtos durante a baixa de estoque.
            for produto_id, quantidade in quantidades_por_produto.items():
                produto = Produto.query.filter_by(
                    id=produto_id
                ).with_for_update().first()

                if produto is None:
                    raise ValueError("Um dos produtos não foi encontrado.")

                estoque = Decimal(produto.estoque or 0)
                if quantidade > estoque:
                    raise ValueError(
                        "Estoque insuficiente para " + produto.nome + "."
                    )

                preco = Decimal(produto.preco or 0)
                custo = Decimal(produto.custo or 0)
                if not (
                    valor_monetario_valido(preco, permite_zero=True)
                    and valor_monetario_valido(custo, permite_zero=True)
                ):
                    raise ValueError("Preço ou custo inválido para esse produto.")
                subtotal = dinheiro_decimal(preco * quantidade)
                custo_total = dinheiro_decimal(custo * quantidade)
                lucro = subtotal - custo_total
                total_geral += subtotal

                itens.append({
                    "produto": produto,
                    "quantidade": quantidade,
                    "estoque": estoque,
                    "preco": preco,
                    "custo": custo,
                    "subtotal": subtotal,
                    "lucro": lucro
                })

            if tipo == "avista":
                pago_total = total_geral
            else:
                try:
                    pago_total = Decimal(
                        request.form.get("entrada", "0").replace(",", ".")
                    )
                except Exception:
                    raise ValueError("Informe um pagamento válido.")

                if (
                    not valor_monetario_valido(pago_total, permite_zero=True)
                    or pago_total > total_geral
                ):
                    raise ValueError(
                        "O pagamento deve ficar entre zero e o total da compra."
                    )

            if novo_nome:
                cliente = Cliente(
                    nome=novo_nome,
                    telefone=novo_telefone
                )
                db.session.add(cliente)
                db.session.flush()
                cliente_id = cliente.id

            data_venda = agora_utc()
            grupo = VendaGrupo(
                cliente_id=cliente_id,
                tipo=tipo,
                total=total_geral,
                pago=pago_total,
                data=data_venda
            )
            db.session.add(grupo)
            db.session.flush()

            restante_pago = pago_total

            for item in itens:
                if tipo == "avista":
                    pago_item = item["subtotal"]
                else:
                    pago_item = min(
                        restante_pago,
                        item["subtotal"]
                    )
                    restante_pago -= pago_item

                nova_venda = Venda(
                    produto_id=item["produto"].id,
                    cliente_id=cliente_id,
                    quantidade=item["quantidade"],
                    preco_unitario=item["preco"],
                    custo_unitario=item["custo"],
                    total=item["subtotal"],
                    lucro=item["lucro"],
                    tipo=tipo,
                    pago=pago_item,
                    data=data_venda
                )
                item["produto"].estoque = (
                    item["estoque"] - item["quantidade"]
                )
                db.session.add(nova_venda)
                db.session.flush()

                db.session.add(VendaGrupoItem(
                    grupo_id=grupo.id,
                    venda_id=nova_venda.id
                ))

            # Unicidade imposta no PostgreSQL para impedir pedidos repetidos.
            db.session.add(VendaRequisicao(
                chave=chave_requisicao, grupo_id=grupo.id
            ))
            db.session.flush()

            if pago_total > 0:
                nome_cliente = (
                    db.session.get(Cliente, cliente_id).nome
                    if cliente_id else "Sem cliente"
                )
                db.session.add(Caixa(
                    tipo="entrada",
                    descricao=(
                        "Compra #" + str(grupo.id)
                        + " - " + nome_cliente
                        + " - " + str(len(itens)) + " item(ns)"
                    ),
                    valor=pago_total,
                    data=data_venda
                ))

            db.session.commit()
            flash(
                "Compra #" + str(grupo.id)
                + " finalizada com "
                + str(len(itens)) + " produto(s).",
                "sucesso"
            )
            return redirect(
                url_for("detalhe_compra", grupo_id=grupo.id)
            )

        except ValueError as erro:
            db.session.rollback()
            flash(str(erro), "erro")
            return redirect("/venda")
        except IntegrityError:
            db.session.rollback()
            pedido_existente = db.session.get(VendaRequisicao, chave_requisicao)
            if pedido_existente:
                flash("Compra já registrada; duplicação bloqueada.", "sucesso")
                return redirect(url_for("detalhe_compra", grupo_id=pedido_existente.grupo_id))
            app.logger.exception("Falha de integridade ao salvar compra")
            flash("Compra não registrada. Confira o histórico.", "erro")
            return redirect("/venda")
        except Exception:
            db.session.rollback()
            app.logger.exception("Falha ao registrar compra com vários itens")
            flash(
                "Não foi possível salvar a compra. Nenhuma alteração foi feita.",
                "erro"
            )
            return redirect("/venda")

    return pagina(r"""
<style>
.produtos-disponiveis {border:1px solid #d5e4d9;border-radius:10px;background:#f8fbf9;margin:2px 0 15px}
.produtos-disponiveis summary {padding:12px;color:#176b3a;font-weight:700;cursor:pointer}
.lista-escolhas {padding:0 9px 10px;max-height:255px;overflow-y:auto;overscroll-behavior:contain}
.produto-escolha {width:100%;display:flex;align-items:center;justify-content:space-between;gap:12px;background:white;color:#222;border:1px solid #dfe6e1;border-radius:9px;margin:6px 0;padding:11px;text-align:left;font-weight:400}
.produto-escolha .produto-nome {font-weight:700;overflow-wrap:anywhere}
.produto-escolha .produto-valores {text-align:right;white-space:nowrap}
.produto-escolha .produto-estoque {display:block;font-size:12px;color:#5d6b60;margin-top:3px}
.produto-escolha[hidden] {display:none}
.produto-escolha:focus-visible {outline:3px solid #176b3a;outline-offset:1px}
.sem-produto {padding:8px 12px;color:#85531d}
.ajuda-selecao {font-size:13px;color:#58655c;margin:-4px 0 8px}
</style>
<h1>Nova venda</h1>
<p>
Adicione todos os produtos que o cliente comprou e finalize uma única compra.
</p>

<form method="post" id="form-venda">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="chave_requisicao" value="{{ chave_requisicao }}">
<div class="formulario" style="max-width:850px">
<h2>Itens da compra</h2>

<div id="itens-venda">
<div class="item-venda" style="border:1px solid #ddd;border-radius:9px;padding:12px;margin-bottom:12px">
<label>Produto</label>
<input class="busca-produto" type="search" placeholder="Pesquisar produto pelo nome..."
       aria-label="Pesquisar produto" autocomplete="off">
<select name="produto" class="produto-venda" required>
<option value="" disabled>Selecione o produto</option>
{% for produto in produtos %}
<option value="{{ produto.id }}" {% if loop.first %}selected{% endif %}
        data-preco="{{ produto.preco }}"
        data-estoque="{{ produto.estoque }}"
        data-unidade="{{ produto.unidade }}">
{{ produto.nome }} — {{ produto.preco|dinheiro }} |
Estoque: {{ produto.estoque|quantidade_legivel }} {{ produto.unidade }}
</option>
{% endfor %}
</select>
<p class="ajuda-selecao">Produtos cadastrados abaixo — toque para selecionar.</p>
<details class="produtos-disponiveis" open>
<summary>Produtos disponíveis ({{ produtos|length }})</summary>
<div class="lista-escolhas">
{% for produto in produtos %}
<button type="button" class="produto-escolha" data-produto-id="{{ produto.id }}">
<span class="produto-nome">{{ produto.nome }}</span>
<span class="produto-valores">{{ produto.preco|dinheiro }}
<span class="produto-estoque">Estoque: {{ produto.estoque|quantidade_legivel }} {{ produto.unidade }}</span>
</span>
</button>
{% else %}
<p class="sem-produto">Nenhum produto cadastrado. Cadastre em Produtos.</p>
{% endfor %}
</div>
<p class="sem-produto nenhum-resultado" hidden>Nenhum produto encontrado na pesquisa.</p>
</details>

<label>Quantidade</label>
<input name="quantidade" class="quantidade-venda"
       type="number" step="0.001" min="0.001" value="1" required>

<div class="detalhes-produto" style="font-size:13px;color:#52645b;margin:0 0 8px">
Escolha um produto para visualizar preço e estoque.
</div>
<div class="subtotal-item" style="font-weight:bold;margin-bottom:8px">
Subtotal: R$ 0,00
</div>
<button type="button" class="remover-item"
        style="display:none;background:#8b1e1e">
Remover produto
</button>
</div>
</div>

<button type="button" id="adicionar-item"
        style="background:#315f93;margin-bottom:18px">
+ Adicionar outro produto
</button>

<div class="card" style="margin-bottom:18px">
<h3>Total da compra</h3>
<div class="valor" id="total-compra">R$ 0,00</div>
</div>

<label>Tipo da venda</label>
<select name="tipo" id="tipo-venda">
<option value="avista">À vista</option>
<option value="fiado">Fiado</option>
</select>

<label for="cliente">Cliente cadastrado</label>
<select name="cliente" id="cliente">
<option value="">Nenhum</option>
{% for cliente in clientes %}
<option value="{{ cliente.id }}">{{ cliente.nome }}</option>
{% endfor %}
</select>

<h2>Cliente novo</h2>
<p>
Se for um novo cliente, deixe “Cliente cadastrado” em Nenhum.
</p>

<label for="novo_cliente_nome">Nome do novo cliente</label>
<input id="novo_cliente_nome" name="novo_cliente_nome" maxlength="150">

<label for="novo_cliente_telefone">Telefone (opcional)</label>
<input id="novo_cliente_telefone" name="novo_cliente_telefone"
       type="tel" maxlength="50">

<div id="area-entrada" hidden>
<label>Valor pago agora (venda fiado)</label>
<input name="entrada" id="entrada-venda"
       type="number" step="0.01" min="0" value="0">
</div>

<button type="submit">Finalizar compra</button>
</div>
</form>

<template id="modelo-item">
<div class="item-venda" style="border:1px solid #ddd;border-radius:9px;padding:12px;margin-bottom:12px">
<label>Produto</label>
<input class="busca-produto" type="search" placeholder="Pesquisar produto pelo nome..."
       aria-label="Pesquisar produto" autocomplete="off">
<select name="produto" class="produto-venda" required>
<option value="" selected disabled>Selecione o produto</option>
{% for produto in produtos %}
<option value="{{ produto.id }}"
        data-preco="{{ produto.preco }}"
        data-estoque="{{ produto.estoque }}"
        data-unidade="{{ produto.unidade }}">
{{ produto.nome }} — {{ produto.preco|dinheiro }} |
Estoque: {{ produto.estoque|quantidade_legivel }} {{ produto.unidade }}
</option>
{% endfor %}
</select>
<p class="ajuda-selecao">Produtos cadastrados abaixo — toque para selecionar.</p>
<details class="produtos-disponiveis" open>
<summary>Produtos disponíveis ({{ produtos|length }})</summary>
<div class="lista-escolhas">
{% for produto in produtos %}
<button type="button" class="produto-escolha" data-produto-id="{{ produto.id }}">
<span class="produto-nome">{{ produto.nome }}</span>
<span class="produto-valores">{{ produto.preco|dinheiro }}
<span class="produto-estoque">Estoque: {{ produto.estoque|quantidade_legivel }} {{ produto.unidade }}</span>
</span>
</button>
{% else %}
<p class="sem-produto">Nenhum produto cadastrado. Cadastre em Produtos.</p>
{% endfor %}
</div>
<p class="sem-produto nenhum-resultado" hidden>Nenhum produto encontrado na pesquisa.</p>
</details>

<label>Quantidade</label>
<input name="quantidade" class="quantidade-venda"
       type="number" step="0.001" min="0.001" value="1" required>

<div class="detalhes-produto" style="font-size:13px;color:#52645b;margin:0 0 8px">
Escolha um produto para visualizar preço e estoque.
</div>
<div class="subtotal-item" style="font-weight:bold;margin-bottom:8px">
Subtotal: R$ 0,00
</div>
<button type="button" class="remover-item"
        style="background:#8b1e1e">
Remover produto
</button>
</div>
</template>

<script>
(function(){
    const itens = document.getElementById("itens-venda");
    const modelo = document.getElementById("modelo-item");
    const adicionar = document.getElementById("adicionar-item");
    const total = document.getElementById("total-compra");
    const tipo = document.getElementById("tipo-venda");
    const areaEntrada = document.getElementById("area-entrada");

    function normalizar(texto) {
        return String(texto || "").normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "").toLowerCase();
    }
    function filtrarProdutos(linha){
        const campo = linha.querySelector(".busca-produto");
        const termo = normalizar(campo.value.trim());
        const botoes = linha.querySelectorAll(".produto-escolha");
        let visiveis = 0;
        botoes.forEach((botao)=>{
            const mostrar = normalizar(botao.querySelector(".produto-nome").textContent)
                .includes(termo);
            botao.hidden = !mostrar;
            if (mostrar) visiveis += 1;
        });
        linha.querySelector(".nenhum-resultado").hidden = visiveis > 0;
        if (termo) linha.querySelector(".produtos-disponiveis").open = true;
    }

    function moeda(valor){
        return valor.toLocaleString("pt-BR", {
            style: "currency",
            currency: "BRL"
        });
    }

    function atualizar(){
        let soma = 0;

        itens.querySelectorAll(".item-venda").forEach((linha)=>{
            const select = linha.querySelector(".produto-venda");
            const quantidade = linha.querySelector(".quantidade-venda");
            const opcao = select.selectedIndex >= 0 ?
                select.options[select.selectedIndex] : null;
            const preco = opcao && select.value ?
                Number(opcao.dataset.preco || 0) : 0;
            const qtd = Number(quantidade.value || 0);
            const subtotal = preco * Math.max(qtd, 0);
            const detalhes = linha.querySelector(".detalhes-produto");
            if (opcao && select.value) {
                const estoque = Number(opcao.dataset.estoque || 0);
                const unidade = opcao.dataset.unidade || "un";
                detalhes.textContent = "Preço: " + moeda(preco) +
                    " • Estoque: " + estoque.toLocaleString("pt-BR", {
                        maximumFractionDigits: 3
                    }) + " " + unidade;
            } else {
                detalhes.textContent = "Escolha um produto da lista.";
            }
            linha.querySelector(".subtotal-item").textContent =
                "Subtotal: " + moeda(subtotal);
            soma += subtotal;
        });

        total.textContent = moeda(soma);
        areaEntrada.hidden = tipo.value !== "fiado";
    }

    function preparar(linha){
        linha.querySelector(".busca-produto").addEventListener(
            "input", ()=>filtrarProdutos(linha)
        );
        linha.querySelectorAll(".produto-escolha").forEach((botao)=>{
            botao.addEventListener("click", ()=>{
                const seletor = linha.querySelector(".produto-venda");
                seletor.value = botao.dataset.produtoId;
                linha.querySelector(".busca-produto").value = "";
                filtrarProdutos(linha);
                linha.querySelector(".produtos-disponiveis").open = false;
                atualizar();
            });
        });
        linha.querySelector(".produto-venda").addEventListener(
            "change", atualizar
        );
        linha.querySelector(".quantidade-venda").addEventListener(
            "input", atualizar
        );

        const remover = linha.querySelector(".remover-item");
        remover.addEventListener("click", ()=>{
            linha.remove();
            atualizar();
        });
    }

    preparar(itens.querySelector(".item-venda"));

    adicionar.addEventListener("click", ()=>{
        const fragmento = modelo.content.cloneNode(true);
        const linha = fragmento.querySelector(".item-venda");
        itens.appendChild(fragmento);
        preparar(linha);
        atualizar();
    });

    // Confirma a lista completa antes de gravar: evita trocar um item
    // por outro inadvertidamente e não altera movimentos já lançados.
    document.getElementById("form-venda").addEventListener("submit", (evento)=>{
        const linhas = Array.from(itens.querySelectorAll(".item-venda"));
        const ids = [];
        const resumo = [];
        let valor = 0;
        for (const linha of linhas){
            const select = linha.querySelector(".produto-venda");
            const quantidade = Number(linha.querySelector(".quantidade-venda").value);
            const opcao = select.options[select.selectedIndex];
            if (!opcao || !select.value || !Number.isFinite(quantidade) || quantidade <= 0){
                evento.preventDefault();
                return;
            }
            const preco = Number(opcao.dataset.preco || 0);
            const nome = opcao.textContent.split("—")[0].trim();
            const unidade = opcao.dataset.unidade || "un";
            ids.push(select.value);
            resumo.push(nome + " — " + quantidade.toLocaleString("pt-BR") +
                " " + unidade + " — " + moeda(preco * quantidade));
            valor += preco * quantidade;
        }
        const clienteSelecionado = document.getElementById("cliente");
        const nomeNovo = document.getElementById("novo_cliente_nome").value.trim();
        const cliente = nomeNovo || clienteSelecionado.options[clienteSelecionado.selectedIndex].textContent.trim();
        const repetido = ids.length !== new Set(ids).size;
        const aviso = repetido ?
            "\nATENÇÃO: o mesmo produto está repetido e as quantidades serão somadas.\n" : "";
        const mensagem = "CONFIRA ANTES DE FINALIZAR\n\nCliente: " + cliente +
            "\n\n" + resumo.join("\n") + "\n\nTOTAL: " + moeda(valor) +
            "\nPagamento: " + (tipo.value === "fiado" ? "Fiado" : "À vista") +
            aviso + "\n\nOs produtos e as quantidades estão corretos?";
        if (!window.confirm(mensagem)){
            evento.preventDefault();
            return;
        }
        // Impede toque duplo e dois lançamentos acidentais.
        const botao = evento.currentTarget.querySelector('button[type="submit"]');
        if (botao) {
            botao.disabled = true;
            botao.textContent = "Registrando compra...";
        }
    });

    tipo.addEventListener("change", atualizar);
    atualizar();
})();
</script>
""", produtos=produtos, clientes=clientes,
        chave_requisicao=secrets.token_urlsafe(32))


@app.route("/vendas/compra/<int:grupo_id>")
def detalhe_compra(grupo_id):
    if not logado():
        return redirect("/login")

    grupo = db.get_or_404(VendaGrupo, grupo_id)

    vinculos = VendaGrupoItem.query.filter_by(
        grupo_id=grupo.id
    ).order_by(VendaGrupoItem.id).all()

    vendas_excluidas = {
        item.venda_id
        for item in ExclusaoVenda.query.filter(
            ExclusaoVenda.venda_id.in_(
                [v.venda_id for v in vinculos] or [-1]
            )
        ).all()
    }

    itens = [
        vinculo.venda
        for vinculo in vinculos
        if vinculo.venda_id not in vendas_excluidas
    ]

    total_atual = sum(
        (Decimal(v.total or 0) for v in itens),
        Decimal("0")
    )
    lucro_atual = sum(
        (Decimal(v.lucro or 0) for v in itens),
        Decimal("0")
    )

    return pagina("""
<h1>Compra #{{ grupo.id }}</h1>
<p class="compras-ajuda">Confira cada produto registrado nessa compra.</p>
<div class="compra-card" style="max-width:720px">
<div class="compra-topo">
<div>
<div class="compra-cliente">{{ grupo.cliente.nome if grupo.cliente else "Sem cliente" }}</div>
<time class="compra-data">{{ (grupo.data|data_local).strftime("%d/%m/%Y às %H:%M") }}</time>
</div>
<span class="compra-situacao {{ 'fiado' if grupo.tipo == 'fiado' else '' }}">{{ "Fiado" if grupo.tipo == "fiado" else "À vista" }}</span>
</div>
<ul class="compra-itens">
{% for venda in itens %}
<li class="compra-item">
<div>
<span class="compra-item-nome">{{ venda.produto.nome }}</span>
<span class="compra-item-dados">{{ venda.quantidade|quantidade_legivel }} {{ venda.produto.unidade }} × {{ venda.preco_unitario|dinheiro }}</span>
</div>
<div style="text-align:right">
<span class="compra-item-total">{{ venda.total|dinheiro }}</span>
<span class="compra-item-dados">Lucro: {{ venda.lucro|dinheiro }}</span>
</div>
</li>
{% else %}
<li class="compra-item">Nenhum item ativo nesta compra.</li>
{% endfor %}
</ul>
<div class="compra-rodape">
<div class="compra-totais">
<div><small>Total registrado</small><strong>{{ total_atual|dinheiro }}</strong></div>
<div><small>Lucro bruto registrado</small><strong class="lucro">{{ lucro_atual|dinheiro }}</strong></div>
</div>
<p style="margin:14px 0 0"><small>Pago no momento do registro: {{ grupo.pago|dinheiro }}</small></p>
</div>
</div>
<div class="aviso" style="max-width:720px;margin-top:16px">
Se algum produto não aparece nesta lista, ele não consta entre os itens ativos
desta compra. Confira o histórico antes de registrar novamente, para não duplicar
estoque, caixa ou vendas.
</div>
<p>
<a href="/venda">+ Nova venda</a>
&nbsp;•&nbsp;
<a href="/vendas">Ver todas as vendas</a>
&nbsp;•&nbsp;
<a href="/">Voltar ao painel</a>
</p>
""",
        grupo=grupo,
        itens=itens,
        total_atual=total_atual,
        lucro_atual=lucro_atual
    )


@app.route("/vendas")
def listar_vendas():
    if not logado():
        return redirect("/login")
    pagina_numero = max(1, request.args.get("pagina", 1, type=int) or 1)
    lista = vendas_validas().order_by(Venda.id.desc()).paginate(
        page=pagina_numero, per_page=50, error_out=False
    )
    return pagina("""
<h1>Vendas / Excluir</h1>
<p>Localize a venda e clique em Excluir para conferir os detalhes.</p>
<div class="tabela"><table>
<tr><th>Nº / Data</th><th>Cliente</th><th>Produto</th><th>Quantidade</th>
<th>Total</th><th>Pago</th><th>Ação</th></tr>
{% for v in lista.items %}
<tr><td>#{{ v.id }}<br>{{ (v.data|data_local).strftime('%d/%m/%Y %H:%M') }}</td>
<td>{{ v.cliente.nome if v.cliente else 'Sem cliente' }}</td>
<td>{{ v.produto.nome }}</td><td>{{ v.quantidade }} {{ v.produto.unidade }}</td>
<td>{{ v.total|dinheiro }}</td><td>{{ v.pago|dinheiro }}</td>
<td><a href="{{ url_for('excluir_venda', venda_id=v.id) }}">Excluir</a></td></tr>
{% else %}<tr><td colspan="7">Nenhuma venda encontrada.</td></tr>{% endfor %}
</table></div>
<p>{% if lista.has_prev %}<a href="{{ url_for('listar_vendas', pagina=lista.prev_num) }}">Anterior</a>{% endif %}
 Página {{ lista.page }}
{% if lista.has_next %}<a href="{{ url_for('listar_vendas', pagina=lista.next_num) }}">Próxima</a>{% endif %}</p>
""", lista=lista)


@app.route("/vendas/<int:venda_id>/excluir", methods=["GET", "POST"])
def excluir_venda(venda_id):
    if not logado():
        return redirect("/login")
    if request.method == "POST":
        esperado = session.get("csrf_excluir_venda", "")
        recebido = request.form.get("csrf_excluir_venda", "")
        if not esperado or not hmac.compare_digest(recebido, esperado):
            return "Formulário expirado. Volte à lista de vendas e tente novamente.", 400
        if request.form.get("confirmar") != "sim":
            return "Confirme a exclusão da venda.", 400

    # O bloqueio serializa a exclusão e os recebimentos de fiado.
    venda = Venda.query.filter_by(id=venda_id).with_for_update().first_or_404()
    if db.session.get(ExclusaoVenda, venda_id):
        flash("Esta venda já foi excluída.", "erro")
        return redirect("/vendas")

    if request.method == "POST":
        # Exige nova conferência caso um pagamento tenha ocorrido após abrir a tela.
        if request.form.get("pago_conferido") != str(venda.pago):
            flash("O valor pago mudou. Confira os detalhes novamente.", "erro")
            return redirect(request.path)
        try:
            db.session.add(ExclusaoVenda(venda_id=venda.id))
            db.session.execute(db.update(Produto).where(
                Produto.id == venda.produto_id
            ).values(estoque=Produto.estoque + venda.quantidade))
            if venda.pago > 0:
                db.session.add(Caixa(
                    tipo="saida",
                    descricao="Estorno por exclusão da venda #" + str(venda.id),
                    valor=venda.pago
                ))
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Falha ao excluir venda %s", venda_id)
            flash("Não foi possível excluir a venda. Nenhuma alteração foi salva.", "erro")
            return redirect("/vendas")
        flash("Venda excluída. Estoque devolvido e valores atualizados.", "sucesso")
        return redirect("/vendas")

    if "csrf_excluir_venda" not in session:
        session["csrf_excluir_venda"] = secrets.token_urlsafe(32)
    return pagina("""
<div class="formulario">
<h1>Excluir venda #{{ v.id }}</h1>
<p>{{ (v.data|data_local).strftime('%d/%m/%Y %H:%M') }}</p>
<p><strong>Cliente:</strong> {{ v.cliente.nome if v.cliente else 'Sem cliente' }}</p>
<p><strong>Produto:</strong> {{ v.produto.nome }} — {{ v.quantidade }} {{ v.produto.unidade }}</p>
<p><strong>Total:</strong> {{ v.total|dinheiro }} · <strong>Pago:</strong> {{ v.pago|dinheiro }}</p>
<div class="aviso">
<p>A venda sairá das listas e dos totais de vendas e lucro nos relatórios.
Os produtos voltarão ao estoque e o fiado pendente será removido.</p>
<p>Será registrada uma saída de <strong>{{ v.pago|dinheiro }}</strong> no caixa atual
para estornar o que já foi recebido, mesmo se a venda for de um caixa anterior.
Confira também a devolução do dinheiro ao cliente, quando houver.</p>
<p>O registro original será guardado para conferência. Esta ação não pode ser desfeita pela tela.</p>
</div>
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="csrf_excluir_venda" value="{{ token }}">
<input type="hidden" name="pago_conferido" value="{{ v.pago }}">
<label><input style="width:auto" type="checkbox" name="confirmar" value="sim" required>
Conferi a venda e confirmo a exclusão e o estorno.</label>
<p><button style="background:#a32020" type="submit">Confirmar exclusão</button>
<a href="/vendas" style="display:inline-block;padding:12px">Cancelar</a></p>
</form></div>
""", v=venda, token=session["csrf_excluir_venda"])


@app.route("/fiado")
def fiado():
    if not logado():
        return redirect("/login")

    vendas = vendas_validas().filter(
        Venda.tipo == "fiado"
    ).order_by(Venda.id.desc()).all()

    # Agrupa somente na apresentação. Cada venda mantém seu ID, saldo e
    # recebimento próprios; clientes homônimos não têm dívidas misturadas.
    # Fiados com saldo positivo ficam em "A receber". Qualquer valor já
    # pago (inclusive parcial/entrada) aparece na tabela "Recebidos".
    # Uma venda parcialmente paga pode aparecer nas duas seções; os totais
    # mostram valores diferentes e não são somados duas vezes.
    recebidos = [v for v in vendas if Decimal(v.pago or 0) > 0]
    pendentes = [v for v in vendas if v.saldo_devedor > 0]
    quitados = sum(1 for v in vendas if v.saldo_devedor <= 0)

    por_cliente = {}
    for venda in pendentes:
        chave = venda.cliente_id if venda.cliente_id is not None else ("sem_cliente", venda.id)
        if chave not in por_cliente:
            por_cliente[chave] = {
                "nome": venda.cliente.nome if venda.cliente else "Sem cliente",
                "vendas": [],
                "total": Decimal("0"),
                "pago": Decimal("0"),
                "saldo": Decimal("0"),
            }
        grupo = por_cliente[chave]
        grupo["vendas"].append(venda)
        grupo["total"] += Decimal(venda.total or 0)
        grupo["pago"] += Decimal(venda.pago or 0)
        grupo["saldo"] += venda.saldo_devedor

    grupos = list(por_cliente.values())
    total_geral = sum((Decimal(v.total or 0) for v in vendas), Decimal("0"))
    pago_geral = sum((Decimal(v.pago or 0) for v in vendas), Decimal("0"))
    saldo_geral = sum((v.saldo_devedor for v in vendas), Decimal("0"))

    return pagina("""
<style>
.fiado-resumo,.fiado-balanco{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.fiado-indicador{background:#f7faf8;border:1px solid #dce9df;border-radius:10px;padding:12px;min-width:0}
.fiado-indicador small{display:block;color:#53645b;font-size:12px;margin-bottom:5px}
.fiado-indicador strong{display:block;font-size:17px;overflow-wrap:anywhere}
.fiado-devedor strong{color:#ae2631}
.fiado-pesquisa{margin:18px 0}
.fiado-pesquisa label{display:block;font-weight:600;margin-bottom:7px}
.fiado-pesquisa input{width:100%;max-width:100%;padding:12px;border:1px solid #cbd7cf;border-radius:9px;font:inherit}
.fiado-pesquisa small{display:block;margin-top:6px;color:#58665e}
.fiado-cartao{background:#fff;border:1px solid #dfe6e1;border-radius:14px;margin:16px 0;overflow:hidden;box-shadow:0 2px 7px #00000009}
.fiado-cabecalho{padding:15px;background:#eef7f0;border-bottom:1px solid #dfe6e1;display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}
.fiado-cabecalho h2{font-size:20px;margin:0;color:#176b3a}
.fiado-cabecalho small{color:#52655a}
.fiado-balanco{padding:12px}
.fiado-produto{border-top:1px solid #e9eeea;padding:12px 15px;display:grid;grid-template-columns:minmax(0,1.7fr) repeat(3,minmax(0,1fr));gap:10px;align-items:start}
.fiado-produto:first-child{border-top:1px solid #dfe6e1}
.fiado-produto-nome strong{display:block;overflow-wrap:anywhere}
.fiado-produto-nome small{color:#68756d;font-size:12px}
.fiado-valor small{display:block;color:#68756d;font-size:12px;margin-bottom:4px}
.fiado-valor strong{overflow-wrap:anywhere}
.fiado-pagamento{grid-column:1/-1;display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap}
.fiado-pagamento input{width:180px;max-width:100%;padding:10px}
.fiado-pagamento button{margin:0}
.fiado-quitado{color:#176b3a;font-weight:700}
.fiado-secao{margin-top:24px}
.fiado-secao h2{font-size:20px;margin:0 0 7px}
.fiado-secao p{color:#59675e;margin:5px 0 12px}
.fiado-recebido{color:#176b3a;font-weight:700}
.fiado-table-wrapper{background:#fff;border:1px solid #dfe6e1;border-radius:14px;overflow-x:auto}
.fiado-table-wrapper table{width:100%;border-collapse:collapse}
.fiado-table-wrapper th,.fiado-table-wrapper td{padding:13px;text-align:left;border-bottom:1px solid #e7ebe8;vertical-align:middle;overflow-wrap:anywhere}
.fiado-table-wrapper th{background:#eef7f0}
.fiado-table-wrapper td strong{display:block}
.fiado-table-wrapper td small{color:#647268}
.fiado-status{font-weight:700;color:#176b3a}
.fiado-status-parcial{color:#a66b00}
.fiado-tabela-vazia{padding:16px}
@media(max-width:640px){
  .fiado-table-wrapper table,.fiado-table-wrapper tbody,.fiado-table-wrapper tr,.fiado-table-wrapper td{display:block;width:100%}
  .fiado-table-wrapper thead{display:none}
  .fiado-table-wrapper tr{padding:9px 12px;border-bottom:1px solid #dfe6e1}
  .fiado-table-wrapper td{display:flex;justify-content:space-between;gap:10px;padding:7px 3px;border:0;text-align:right}
  .fiado-table-wrapper td::before{content:attr(data-label);font-weight:bold;color:#5b6d61;text-align:left}
  .fiado-table-wrapper td.fiado-tabela-vazia{display:block;text-align:left}
  .fiado-table-wrapper td.fiado-tabela-vazia::before{display:none}
}
@media(max-width:640px){
  .fiado-resumo,.fiado-balanco{grid-template-columns:1fr 1fr;gap:7px}
  .fiado-resumo .fiado-devedor,.fiado-balanco .fiado-devedor{grid-column:1/-1}
  .fiado-indicador{padding:10px}
  .fiado-produto{grid-template-columns:1fr 1fr;gap:12px}
  .fiado-produto-nome{grid-column:1/-1}
  .fiado-pagamento{justify-content:stretch}
  .fiado-pagamento input{flex:1;min-width:0}
}
</style>
<h1>Contas fiado</h1>
<p><a href="/auditoria-fiado"><strong>Ver auditoria detalhada dos fiados por cliente</strong></a>
 · <a href="/auditoria-fiado?formato=pdf"><strong>Baixar PDF</strong></a></p>
<div class="fiado-resumo">
  <div class="fiado-indicador"><small>Total das compras</small><strong>{{ total_geral|dinheiro }}</strong></div>
  <div class="fiado-indicador"><small>Total recebido</small><strong>{{ pago_geral|dinheiro }}</strong></div>
  <div class="fiado-indicador fiado-devedor"><small>Total a receber</small><strong>{{ saldo_geral|dinheiro }}</strong></div>
</div>
<div class="fiado-pesquisa">
  <label for="buscar-fiado">🔎 Buscar cliente ou produto</label>
  <input id="buscar-fiado" type="search" placeholder="Digite o nome do cliente ou produto..." autocomplete="off">
  <small id="fiado-quantidade" aria-live="polite">{{ grupos|length }} cliente(s) a receber · {{ recebidos|length }} venda(s) com recebimento</small>
</div>
<section class="fiado-secao" id="fiados-a-receber">
<h2>🔴 Fiados a receber</h2>
<p>Clientes que ainda devem. Pagamentos parciais já são descontados do saldo.</p>
{% for grupo in grupos %}
<article class="fiado-cartao" data-fiado-cartao>
  <div class="fiado-cabecalho">
    <h2>{{ grupo.nome }}</h2>
    <small>{{ grupo.vendas|length }} compra(s)</small>
  </div>
  <div class="fiado-balanco">
    <div class="fiado-indicador"><small>Total</small><strong>{{ grupo.total|dinheiro }}</strong></div>
    <div class="fiado-indicador"><small>Pago</small><strong>{{ grupo.pago|dinheiro }}</strong></div>
    <div class="fiado-indicador fiado-devedor"><small>Falta pagar</small><strong>{{ grupo.saldo|dinheiro }}</strong></div>
  </div>
  <div class="fiado-itens">
    {% for venda in grupo.vendas %}
    <div class="fiado-produto">
      <div class="fiado-produto-nome"><strong>{{ venda.produto.nome }}</strong><small>Venda #{{ venda.id }}</small></div>
      <div class="fiado-valor"><small>Valor</small><strong>{{ venda.total|dinheiro }}</strong></div>
      <div class="fiado-valor"><small>Pago</small><strong>{{ venda.pago|dinheiro }}</strong></div>
      <div class="fiado-valor"><small>Falta pagar</small><strong>{{ venda.saldo_devedor|dinheiro }}</strong></div>
      <div class="fiado-pagamento">
        {% if venda.saldo_devedor > 0 %}
        <form method="post" action="/receber/{{ venda.id }}">
          <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
          <input aria-label="Valor recebido da venda {{ venda.id }}" name="valor" type="number"
                 min="0.01" step="0.01" max="{{ venda.saldo_devedor }}" placeholder="Valor recebido" required>
          <button type="submit">Receber</button>
        </form>
        {% else %}
        <span class="fiado-quitado">✅ Pago</span>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
</article>
{% else %}
<p>Não há fiados pendentes.</p>
{% endfor %}
</section>
<section class="fiado-secao" id="fiados-recebidos">
<h2>🟢 Fiados recebidos</h2>
<p>Pagamentos registrados, inclusive parciais. {{ quitados }} venda(s) totalmente quitada(s). Uma compra parcialmente paga também aparece em “A receber” pelo valor restante.</p>
<div class="fiado-table-wrapper"><table>
<thead><tr><th>Cliente</th><th>Produto</th><th>Valor da compra</th><th>Recebido</th><th>Falta pagar</th><th>Status</th></tr></thead>
<tbody>
{% for venda in recebidos %}
<tr data-fiado-recebido>
  <td data-label="Cliente">{{ venda.cliente.nome if venda.cliente else "Sem cliente" }}</td>
  <td data-label="Produto"><strong>{{ venda.produto.nome }}</strong><small>Venda #{{ venda.id }}</small></td>
  <td data-label="Total">{{ venda.total|dinheiro }}</td>
  <td data-label="Recebido" class="fiado-recebido">{{ venda.pago|dinheiro }}</td>
  <td data-label="Falta pagar">{{ venda.saldo_devedor|dinheiro }}</td>
  <td data-label="Situação">
    {% if venda.saldo_devedor > 0 %}
    <span class="fiado-status fiado-status-parcial">Parcial</span>
    {% else %}
    <span class="fiado-status">✅ Quitado</span>
    {% endif %}
  </td>
</tr>
{% else %}
<tr><td colspan="6" class="fiado-tabela-vazia">Nenhum pagamento de fiado registrado.</td></tr>
{% endfor %}
</tbody>
</table></div>
</section>
<script>
(function () {
  const busca = document.getElementById("buscar-fiado");
  const cartoes = Array.from(document.querySelectorAll("[data-fiado-cartao]"));
  const recebidos = Array.from(document.querySelectorAll("[data-fiado-recebido]"));
  const contador = document.getElementById("fiado-quantidade");
  const normalizar = texto => (texto || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  busca.addEventListener("input", function () {
    const termo = normalizar(busca.value.trim());
    let visiveis = 0;
    cartoes.forEach(function (cartao) {
      const mostrar = !termo || normalizar(cartao.textContent).includes(termo);
      cartao.hidden = !mostrar;
      if (mostrar) visiveis++;
    });
    let recebimentosVisiveis = 0;
    recebidos.forEach(function (linha) {
      const mostrar = !termo || normalizar(linha.textContent).includes(termo);
      linha.hidden = !mostrar;
      if (mostrar) recebimentosVisiveis++;
    });
    contador.textContent = visiveis + " de " + cartoes.length + " cliente(s) a receber · "
      + recebimentosVisiveis + " de " + recebidos.length + " venda(s) com recebimento";
  });
})();
</script>
""", grupos=grupos, recebidos=recebidos, quitados=quitados,
       total_geral=total_geral, pago_geral=pago_geral, saldo_geral=saldo_geral)


@app.route("/auditoria-fiado")
def auditoria_fiado():
    """Conferencia de fiados por cliente. Somente leitura, inclusive na exportacao."""
    if not logado():
        return redirect("/login")

    import csv
    from io import StringIO

    vendas = vendas_validas().filter(Venda.tipo == "fiado").order_by(Venda.id.desc()).all()
    limite_venda = db.session.query(
        db.func.max(FechamentoCaixa.ultima_venda_id)
    ).scalar() or 0
    grupo_por_venda = {
        vinculo.venda_id: vinculo.grupo_id
        for vinculo in VendaGrupoItem.query.all()
    }
    clientes_por_id = {}
    alertas = []
    fiado_antigo = Decimal("0")
    fiado_atual = Decimal("0")
    total_fiado = Decimal("0")
    total_pago = Decimal("0")

    for venda in vendas:
        total = Decimal(venda.total or 0)
        pago = Decimal(venda.pago or 0)
        saldo = venda.saldo_devedor
        total_fiado += total
        total_pago += pago
        if venda.id <= limite_venda:
            fiado_antigo += saldo
        else:
            fiado_atual += saldo
        if venda.cliente_id not in clientes_por_id:
            clientes_por_id[venda.cliente_id] = {
                "id": venda.cliente_id,
                "nome": venda.cliente.nome if venda.cliente else "SEM CLIENTE",
                "total": Decimal("0"),
                "pago": Decimal("0"),
                "saldo": Decimal("0"),
                "compras": set(),
                "itens": [],
            }
        cliente = clientes_por_id[venda.cliente_id]
        cliente["total"] += total
        cliente["pago"] += pago
        cliente["saldo"] += saldo
        grupo_id = grupo_por_venda.get(venda.id)
        cliente["compras"].add(
            ("grupo", grupo_id) if grupo_id is not None else ("venda", venda.id)
        )
        cliente["itens"].append(venda)
        if venda.cliente_id is None and saldo > 0:
            alertas.append(
                "Venda fiada #" + str(venda.id) + " tem saldo pendente sem cliente cadastrado."
            )
        if total < 0 or pago < 0 or pago > total:
            alertas.append(
                "Venda #" + str(venda.id) + ": total ou pagamento fora dos limites."
            )

    # Recibos gravados no caixa: confirmam os pagamentos posteriores ao pedido.
    recibos_por_venda = {}
    for movimento in Caixa.query.filter(Caixa.tipo == "entrada").all():
        descricao = movimento.descricao or ""
        prefixo = "Pagamento de fiado - Venda #"
        if not descricao.startswith(prefixo):
            continue
        try:
            venda_id = int(descricao[len(prefixo):])
        except ValueError:
            alertas.append("Recibo de fiado com identificacao invalida no caixa #" + str(movimento.id))
            continue
        recibos_por_venda.setdefault(venda_id, []).append(movimento)

    todas_vendas = {v.id: v for v in Venda.query.all()}
    excluidas = {ex.venda_id for ex in ExclusaoVenda.query.all()}
    for venda_id, recibos in recibos_por_venda.items():
        if venda_id not in todas_vendas:
            alertas.append("Recebimento no caixa para venda inexistente #" + str(venda_id))
        elif venda_id in excluidas:
            alertas.append("Venda excluida #" + str(venda_id) + " tem recebimento: conferir estorno.")
        for i, anterior in enumerate(recibos):
            for seguinte in recibos[i + 1:]:
                if (
                    Decimal(anterior.valor or 0) == Decimal(seguinte.valor or 0)
                    and abs((anterior.data - seguinte.data).total_seconds()) <= 120
                ):
                    alertas.append(
                        "Conferir possivel recebimento repetido da venda #"
                        + str(venda_id) + " nos movimentos #"
                        + str(anterior.id) + " e #" + str(seguinte.id)
                    )

    vinculos_por_grupo = {}
    for venda_id, grupo_id in grupo_por_venda.items():
        vinculos_por_grupo.setdefault(grupo_id, []).append(venda_id)
    for grupo in VendaGrupo.query.filter(VendaGrupo.tipo == "fiado").all():
        ids = vinculos_por_grupo.get(grupo.id, [])
        if not ids or any(i in excluidas for i in ids):
            continue  # Pedido com exclusao requer conferencia na auditoria financeira geral.
        itens = [todas_vendas[i] for i in ids if i in todas_vendas]
        if len(itens) != len(ids):
            alertas.append("Compra #" + str(grupo.id) + ": faltam itens vinculados.")
            continue
        pago_itens = sum((Decimal(v.pago or 0) for v in itens), Decimal("0"))
        pago_inicial = Decimal(grupo.pago or 0)
        recebido_depois = sum((
            Decimal(m.valor or 0)
            for v in itens
            for m in recibos_por_venda.get(v.id, [])
        ), Decimal("0"))
        if abs(pago_itens - pago_inicial - recebido_depois) > Decimal("0.01"):
            alertas.append(
                "Compra #" + str(grupo.id)
                + ": pagamentos das vendas nao conciliam com entrada inicial e recibos."
            )

    clientes = sorted(
        clientes_por_id.values(),
        key=lambda c: (-c["saldo"], c["nome"].casefold(), c["id"] or 0)
    )
    por_nome = {}
    for cliente in clientes:
        chave = cliente["nome"].strip().casefold()
        if cliente["id"] is not None and chave:
            por_nome.setdefault(chave, set()).add(cliente["id"])
    for nome, ids in por_nome.items():
        if len(ids) > 1:
            alertas.append(
                "Nome de cliente cadastrado em " + str(len(ids))
                + " registros diferentes (" + nome + "). Conferir se e a mesma pessoa."
            )

    if request.args.get("formato") == "pdf":
        from fiado_pdf import gerar_pdf_fiado

        pdf = gerar_pdf_fiado(
            clientes=clientes, grupo_por_venda=grupo_por_venda,
            total_fiado=total_fiado, total_pago=total_pago,
            fiado_antigo=fiado_antigo, fiado_atual=fiado_atual,
            alertas=alertas, formata_dinheiro=dinheiro,
            data_local=data_local, fuso_loja=FUSO_LOJA,
        )
        return Response(
            pdf, mimetype="application/pdf",
            headers={
                "Content-Disposition": "attachment; filename=auditoria-fiado.pdf",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            }
        )

    if request.args.get("formato") == "csv":
        arquivo = StringIO()
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow([
            "Venda ID", "Compra ID", "Data", "Cliente ID", "Cliente", "Produto",
            "Total (R$)", "Pago (R$)", "Saldo (R$)", "Periodo"
        ])
        for venda in vendas:
            escritor.writerow([
                venda.id, grupo_por_venda.get(venda.id, ""),
                data_local(venda.data).strftime("%d/%m/%Y %H:%M"),
                venda.cliente_id or "",
                venda.cliente.nome if venda.cliente else "SEM CLIENTE",
                venda.produto.nome if venda.produto else "Produto nao encontrado",
                str(venda.total or 0), str(venda.pago or 0),
                str(venda.saldo_devedor),
                "Anterior ao zeramento" if venda.id <= limite_venda else "Apos zeramento",
            ])
        return Response(
            "\ufeff" + arquivo.getvalue(),
            content_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=auditoria-fiado.csv",
                "Cache-Control": "no-store",
            }
        )

    devedores = sum(1 for cliente in clientes if cliente["saldo"] > 0)
    return pagina("""
<h1>Auditoria detalhada do fiado</h1>
<p>Conferencia somente leitura com todos os fiados ainda existentes no banco,
mesmo de caixas anteriores. Nenhuma venda ou pagamento sera alterado.</p>
<p><a href="/auditoria-fiado?formato=pdf"><strong>Baixar relatório em PDF</strong></a> · <a href="/auditoria-fiado?formato=csv">Baixar planilha CSV de todos os fiados</a>
 · <a href="/auditoria-financeira">Ver auditoria financeira geral</a></p>
<div class="cards">
<div class="card"><h3>Fiado a receber</h3><div class="valor">{{ (antigo + novo)|dinheiro }}</div></div>
<div class="card"><h3>Clientes devedores</h3><div class="valor">{{ devedores }}</div></div>
<div class="card"><h3>Fiado anterior ao ultimo zeramento</h3><div class="valor">{{ antigo|dinheiro }}</div></div>
<div class="card"><h3>Fiado desde ultimo zeramento</h3><div class="valor">{{ novo|dinheiro }}</div></div>
<div class="card"><h3>Total historico de vendas fiadas validas</h3><div class="valor">{{ total|dinheiro }}</div></div>
<div class="card"><h3>Total ja pago nessas vendas</h3><div class="valor">{{ pago|dinheiro }}</div></div>
</div>
<h2>Conferencia por cliente</h2>
<p>Abra o nome de cada cliente para conferir os itens das vendas,
os pagamentos registrados e o saldo devedor.</p>
<div class="tabela"><table>
<tr><th>Cliente</th><th>Compras</th><th>Vendido fiado</th><th>Pago</th><th>Deve</th></tr>
{% for c in clientes %}
<tr>
<td><details><summary><strong>{{ c.nome }}</strong> (ID {{ c.id or "sem cadastro" }})</summary>
<ul>
{% for v in c.itens %}
<li>#{{ v.id }} · {{ (v.data|data_local).strftime("%d/%m/%Y") }}
· {{ v.produto.nome if v.produto else "Produto indisponivel" }}
· {{ v.total|dinheiro }} comprado / {{ v.pago|dinheiro }} pago
/ <strong>{{ v.saldo_devedor|dinheiro }} pendente</strong></li>
{% endfor %}
</ul></details></td>
<td>{{ c.compras|length }}</td>
<td>{{ c.total|dinheiro }}</td>
<td>{{ c.pago|dinheiro }}</td>
<td><strong>{{ c.saldo|dinheiro }}</strong></td>
</tr>
{% else %}
<tr><td colspan="5">Nenhuma venda fiada encontrada.</td></tr>
{% endfor %}
</table></div>
<h2>Alertas para conferir ({{ alertas|length }})</h2>
<p>Os alertas sao suspeitas e nao comprovam fraude ou erro. Os pagamentos
devem ser confrontados com recibos e comprovantes.</p>
{% if alertas %}
<div class="tabela"><table><tr><th>Ponto a conferir</th></tr>
{% for alerta in alertas %}
<tr><td>{{ alerta }}</td></tr>
{% endfor %}</table></div>
{% else %}
<p>Nenhum alerta detectado por estas verificacoes automaticas.</p>
{% endif %}
<p><strong>Limitacao:</strong> o sistema nao registra a data de vencimento
dos fiados nem possui um livro separado de pagamentos por cliente. A
confirmacao final depende dos comprovantes e do historico de recebimentos.</p>
""", clientes=clientes, antigo=fiado_antigo, novo=fiado_atual,
        total=total_fiado, pago=total_pago, devedores=devedores, alertas=alertas)


@app.route("/receber/<int:venda_id>", methods=["POST"])
def receber(venda_id):
    if not logado():
        return redirect("/login")

    venda = Venda.query.filter_by(id=venda_id).with_for_update().first_or_404()
    if db.session.get(ExclusaoVenda, venda_id):
        flash("Esta venda foi excluída e não pode receber pagamentos.", "erro")
        return redirect("/fiado")
    valor = numero(request.form["valor"])

    if not valor_monetario_valido(valor):
        flash("Informe um pagamento positivo, com até 2 casas decimais.", "erro")
        return redirect("/fiado")

    saldo = venda.saldo_devedor
    if valor > saldo:
        flash("Pagamento superior ao saldo devedor. Confira o valor.", "erro")
        return redirect("/fiado")

    venda.pago = Decimal(venda.pago or 0) + valor

    db.session.add(Caixa(
        tipo="entrada",
        descricao="Pagamento de fiado - Venda #" + str(venda.id),
        valor=valor
    ))
    db.session.commit()
    flash("Pagamento recebido.", "sucesso")
    return redirect("/fiado")


@app.route("/caixa", methods=["GET", "POST"])
def caixa():
    if not logado():
        return redirect("/login")

    if request.method == "POST":
        tipo = request.form["tipo"]
        valor = numero(request.form["valor"])

        if tipo not in ("entrada", "saida"):
            flash("Tipo de movimentação inválido.", "erro")
            return redirect("/caixa")
        if not valor_monetario_valido(valor):
            flash("Valor inválido.", "erro")
            return redirect("/caixa")

        movimento = Caixa(
            tipo=tipo,
            descricao=request.form["descricao"],
            valor=valor
        )
        db.session.add(movimento)
        db.session.commit()
        flash("Movimentação registrada.", "sucesso")
        return redirect("/caixa")

    todos_movimentos = movimentos_atuais().all()
    entradas = sum(
        (
            Decimal(m.valor)
            for m in todos_movimentos
            if m.tipo == "entrada"
        ),
        Decimal("0")
    )
    saidas = sum(
        (
            Decimal(m.valor)
            for m in todos_movimentos
            if m.tipo == "saida"
        ),
        Decimal("0")
    )
    saldo = entradas - saidas
    compras = sum(
        (Decimal(m.valor or 0) for m in todos_movimentos
         if eh_compra_de_mercadoria(m)),
        Decimal("0")
    )
    despesas_da_loja = sum(
        (Decimal(m.valor or 0) for m in todos_movimentos
         if eh_despesa_da_loja(m)),
        Decimal("0")
    )
    outras_saidas = saidas - compras - despesas_da_loja
    movimentos = movimentos_atuais().order_by(
        Caixa.id.desc()
    ).limit(100).all()

    return pagina("""
<h1>Caixa</h1>
<p>Valores desde o último zeramento.
<a href="/zerar-caixa">Zerar caixa e vendas</a> · <a href="/relatorios">Baixar relatório financeiro em PDF</a></p>
<p><a href="/despesas">Registrar aluguel e outras despesas da loja</a></p>
<div class="cards">
<div class="card">
<h3>Saldo atual</h3>
<div class="valor">{{ saldo|dinheiro }}</div>
</div>
<div class="card">
<h3>Total de entradas</h3>
<div class="valor">{{ entradas|dinheiro }}</div>
</div>
<div class="card">
<h3>Total de saídas</h3>
<div class="valor despesa">{{ saidas|dinheiro }}</div>
</div>
<div class="card">
<h3>Compras de mercadorias</h3>
<div class="valor despesa">{{ compras|dinheiro }}</div>
</div>
<div class="card">
<h3>Despesas da loja</h3>
<div class="valor despesa">{{ despesas_da_loja|dinheiro }}</div>
</div>
<div class="card">
<h3>Outras saídas / retiradas</h3>
<div class="valor despesa">{{ outras_saidas|dinheiro }}</div>
</div>
</div>

<br>
<div class="formulario">
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<label>Movimento</label>
<select name="tipo">
<option value="entrada">Entrada</option>
<option value="saida">Saída / Despesa</option>
</select>
<label>Descrição</label>
<input name="descricao"
       placeholder="Ex: Energia, frete ou dinheiro colocado no caixa"
       required>
<label>Valor</label>
<input name="valor" type="number" step="0.01" required>
<button>Registrar</button>
</form>
</div>

<h2>Movimentações</h2>
<div class="tabela">
<table>
<tr><th>Data</th><th>Descrição</th><th>Tipo</th><th>Valor</th></tr>
{% for movimento in movimentos %}
<tr>
<td>{{ movimento.data.strftime("%d/%m/%Y %H:%M") }}</td>
<td>{{ movimento.descricao }}</td>
<td>{{ "Entrada" if movimento.tipo == "entrada" else "Saída" }}</td>
<td>{{ movimento.valor|dinheiro }}</td>
</tr>
{% endfor %}
</table>
</div>
""",
        movimentos=movimentos,
        saldo=saldo,
        entradas=entradas,
        saidas=saidas,
        compras=compras,
        despesas_da_loja=despesas_da_loja,
        outras_saidas=outras_saidas
    )


@app.route("/despesas", methods=["GET", "POST"])
def despesas():
    if not logado():
        return redirect("/login")

    if "csrf_despesas" not in session:
        session["csrf_despesas"] = secrets.token_urlsafe(32)

    if request.method == "POST":
        recebido = request.form.get("csrf_despesas", "")
        esperado = session.get("csrf_despesas", "")
        if not esperado or not hmac.compare_digest(recebido, esperado):
            flash("A sessão expirou. Abra o formulário novamente.", "erro")
            return redirect("/despesas")

        categoria = request.form.get("categoria", "").strip()
        detalhes = request.form.get("detalhes", "").strip()
        valor_texto = request.form.get("valor", "").strip()

        if categoria not in CATEGORIAS_DESPESAS:
            flash("Selecione uma categoria válida.", "erro")
            return redirect("/despesas")

        if len(detalhes) > 160:
            flash("Os detalhes devem ter no máximo 160 caracteres.", "erro")
            return redirect("/despesas")

        try:
            valor = Decimal(valor_texto.replace(",", "."))
            if (
                not valor.is_finite()
                or valor <= 0
                or valor > Decimal("9999999999.99")
                or valor != valor.quantize(Decimal("0.01"))
            ):
                raise ValueError()
        except (InvalidOperation, ValueError):
            flash("Informe um valor válido maior que zero, com até 2 casas decimais.", "erro")
            return redirect("/despesas")

        descricao = PREFIXO_DESPESA + categoria
        if detalhes:
            descricao += " - " + detalhes

        db.session.add(Caixa(
            tipo="saida",
            descricao=descricao,
            valor=valor,
            data=agora_utc()
        ))
        db.session.commit()
        flash("Despesa registrada e descontada do caixa.", "sucesso")
        return redirect("/despesas")

    lista = [
        m for m in movimentos_atuais().filter(Caixa.tipo == "saida")
        .order_by(Caixa.id.desc()).all()
        if eh_despesa_da_loja(m)
    ]
    total = sum(
        (Decimal(m.valor or 0) for m in lista), Decimal("0")
    )

    return pagina("""
<h1>Despesas da loja</h1>
<p>Cadastre aluguel, energia, água, internet e outros gastos.
Cada lançamento é uma saída do caixa e fica salvo no histórico.</p>
<div class="cards">
<div class="card">
<h3>🏠 Total de despesas desde o último zeramento</h3>
<div class="valor despesa">{{ total|dinheiro }}</div>
</div>
</div>

<br>
<div class="formulario">
<h2>Adicionar despesa</h2>
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="csrf_despesas" value="{{ token }}">
<label for="categoria">Tipo de despesa</label>
<select id="categoria" name="categoria" required>
{% for categoria in categorias %}
<option value="{{ categoria }}">{{ categoria }}</option>
{% endfor %}
</select>
<label for="detalhes">Descrição ou referência (opcional)</label>
<input id="detalhes" name="detalhes" maxlength="160"
       placeholder="Ex.: Aluguel de outubro de 2026">
<label for="valor">Valor pago (R$)</label>
<input id="valor" name="valor" type="text" inputmode="decimal"
       placeholder="Ex.: 500,00" required>
<p>O lançamento será descontado uma única vez do caixa. Não é cobrança automática mensal.</p>
<button type="submit">Salvar despesa</button>
</form>
</div>

<h2>Histórico de despesas</h2>
<p>Despesas registradas desde o último zeramento.
Para consultar períodos anteriores, acesse <a href="/relatorios">Relatórios</a>.</p>
<div class="tabela">
<table>
<tr><th>Data e horário</th><th>Categoria / descrição</th><th>Valor</th></tr>
{% for movimento in despesas %}
<tr>
<td>{{ (movimento.data|data_local).strftime("%d/%m/%Y %H:%M") }}</td>
<td>{{ movimento.descricao.replace("Despesa da loja - ", "", 1) }}</td>
<td>{{ movimento.valor|dinheiro }}</td>
</tr>
{% else %}
<tr><td colspan="3">Nenhuma despesa cadastrada neste período.</td></tr>
{% endfor %}
</table>
</div>
<p><a href="/caixa">Ver todo o movimento do caixa</a></p>
""", total=total, despesas=lista, categorias=CATEGORIAS_DESPESAS,
        token=session["csrf_despesas"])


@app.route("/zerar-caixa", methods=["GET", "POST"])
def zerar_caixa():
    if not logado():
        return redirect("/login")

    if request.method == "POST":
        recebido = request.form.get("csrf_zerar", "")
        esperado = session.get("csrf_zerar", "")
        if not esperado or not hmac.compare_digest(recebido, esperado):
            flash("A sessão do formulário expirou. Tente novamente.", "erro")
            return redirect("/zerar-caixa")
        if request.form.get("confirmar") != "sim":
            flash("Confirme para iniciar uma nova contagem.", "erro")
            return redirect("/zerar-caixa")

        # Uma única consulta fixa os limites das duas tabelas.
        # Novos registros acima desses IDs pertencem à próxima contagem.
        venda_id, movimento_id = db.session.query(
            db.session.query(db.func.max(Venda.id)).scalar_subquery(),
            db.session.query(db.func.max(Caixa.id)).scalar_subquery()
        ).one()
        db.session.add(FechamentoCaixa(
            ultima_venda_id=venda_id or 0,
            ultimo_movimento_id=movimento_id or 0
        ))
        db.session.commit()
        session.pop("csrf_zerar", None)
        flash(
            "Caixa e vendas zerados. O histórico continua nos relatórios.",
            "sucesso"
        )
        return redirect("/caixa")

    if "csrf_zerar" not in session:
        session["csrf_zerar"] = secrets.token_urlsafe(32)
    return pagina("""
<h1>Zerar caixa e vendas</h1>
<div class="formulario">
<p>O saldo, as entradas, as saídas, o total vendido, o lucro e a lista de
últimas vendas começarão uma nova contagem.</p>
<p>O histórico continuará nos <a href="/relatorios">relatórios</a>.
O estoque e os fiados a receber serão mantidos.</p>
<form method="post">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="csrf_zerar" value="{{ token }}">
<label><input style="width:auto" type="checkbox" name="confirmar"
value="sim" required> Confirmo que desejo iniciar uma nova contagem.</label>
<br><br>
<button type="submit">Zerar caixa e vendas</button>
<a href="/caixa">Cancelar</a>
</form>
</div>
""", token=session["csrf_zerar"])


@app.route("/relatorios")
def relatorios():
    if not logado():
        return redirect("/login")

    periodo = request.args.get("periodo", "diario")

    if periodo not in ("diario", "semanal", "mensal", "personalizado"):
        flash("Período inválido.", "erro")
        return redirect("/relatorios")

    hoje = datetime.now(FUSO_LOJA).strftime("%Y-%m-%d")
    data_inicio = request.args.get("data_inicio", hoje)
    data_fim = request.args.get("data_fim", hoje)
    try:
        def ler_data(valor):
            return datetime.strptime(valor, "%Y-%m-%d").replace(
                tzinfo=FUSO_LOJA
            )

        if periodo == "personalizado":
            inicio_local = ler_data(data_inicio)
            ultimo_dia = ler_data(data_fim)
            if ultimo_dia < inicio_local:
                flash("A data final deve ser igual ou posterior à data inicial.", "erro")
                return redirect("/relatorios")
            fim_local = ultimo_dia + timedelta(days=1)
            referencia = inicio_local
        else:
            referencia = ler_data(request.args.get("data", hoje))
            if periodo == "semanal":
                inicio_local = referencia - timedelta(days=referencia.weekday())
                fim_local = inicio_local + timedelta(days=7)
            elif periodo == "mensal":
                inicio_local = referencia.replace(day=1)
                if inicio_local.month == 12:
                    fim_local = inicio_local.replace(year=inicio_local.year + 1, month=1)
                else:
                    fim_local = inicio_local.replace(month=inicio_local.month + 1)
            else:
                inicio_local = referencia
                fim_local = inicio_local + timedelta(days=1)
    except (ValueError, OverflowError):
        flash("Informe datas válidas para o relatório.", "erro")
        return redirect("/relatorios")

    inicio = inicio_local.astimezone(
        timezone.utc
    ).replace(tzinfo=None)
    fim = fim_local.astimezone(
        timezone.utc
    ).replace(tzinfo=None)

    vendas = vendas_validas().filter(
        Venda.data >= inicio,
        Venda.data < fim
    ).order_by(Venda.data.desc(), Venda.id.desc()).all()

    movimentos = Caixa.query.filter(
        Caixa.data >= inicio,
        Caixa.data < fim
    ).order_by(Caixa.data.desc(), Caixa.id.desc()).all()

    total = sum((v.total for v in vendas), Decimal("0"))
    lucro = sum((v.lucro for v in vendas), Decimal("0"))
    entradas = sum(
        (m.valor for m in movimentos if m.tipo == "entrada"),
        Decimal("0")
    )
    saidas = sum(
        (m.valor for m in movimentos if m.tipo == "saida"),
        Decimal("0")
    )
    fiado_atual = sum(
        (v.saldo_devedor for v in vendas if v.tipo == "fiado"),
        Decimal("0")
    )

    produtos_agrupados = []
    if periodo in ("mensal", "personalizado"):
        resumo_produtos = {}

        for venda in vendas:
            produto = venda.produto
            item = resumo_produtos.setdefault(
                venda.produto_id,
                {
                    "produto": produto.nome,
                    "unidade": produto.unidade,
                    "quantidade": Decimal("0"),
                    "faturamento": Decimal("0"),
                    "lucro": Decimal("0"),
                    "vendas": 0
                }
            )
            item["quantidade"] += Decimal(venda.quantidade or 0)
            item["faturamento"] += Decimal(venda.total or 0)
            item["lucro"] += Decimal(venda.lucro or 0)
            item["vendas"] += 1

        produtos_agrupados = sorted(
            resumo_produtos.values(),
            key=lambda item: (
                item["faturamento"],
                item["quantidade"]
            ),
            reverse=True
        )

    if request.args.get("formato") == "pdf":
        from relatorios_pdf import gerar_pdf_relatorio

        documento = gerar_pdf_relatorio(
            periodo=periodo, inicio_local=inicio_local, fim_local=fim_local,
            vendas=vendas, movimentos=movimentos,
            produtos_agrupados=produtos_agrupados, total=total,
            lucro=lucro, entradas=entradas, saidas=saidas,
            fiado_atual=fiado_atual, formata_dinheiro=dinheiro,
            formata_quantidade=quantidade_legivel,
            data_local=data_local, fuso_loja=FUSO_LOJA,
        )
        nome = ("relatorio-" + periodo + "-"
                + inicio_local.strftime("%Y-%m-%d") + "-a-"
                + (fim_local - timedelta(days=1)).strftime("%Y-%m-%d") + ".pdf")
        return Response(
            documento, mimetype="application/pdf",
            headers={
                "Content-Disposition": "attachment; filename=" + nome,
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return pagina("""
<h1>Relatórios</h1>

<form method="get" class="formulario">
<label for="periodo">Período</label>
<select id="periodo" name="periodo">
{% for valor, titulo in [
    ("diario", "Diário"),
    ("semanal", "Semanal"),
    ("mensal", "Mensal"),
    ("personalizado", "Personalizado")
] %}
<option value="{{ valor }}"
        {% if periodo == valor %}selected{% endif %}>
{{ titulo }}
</option>
{% endfor %}
</select>

<div id="filtro-referencia" {% if periodo == 'personalizado' %}hidden{% endif %}>
<label for="data">Data de referência</label>
<input id="data" name="data" type="date" value="{{ referencia }}"
       {% if periodo != 'personalizado' %}required{% endif %}>
</div>
<div id="filtro-personalizado" {% if periodo != 'personalizado' %}hidden{% endif %}>
<label for="data_inicio">Data inicial</label>
<input id="data_inicio" name="data_inicio" type="date" value="{{ data_inicio }}"
       {% if periodo == 'personalizado' %}required{% endif %}>
<label for="data_fim">Data final</label>
<input id="data_fim" name="data_fim" type="date" value="{{ data_fim }}"
       {% if periodo == 'personalizado' %}required{% endif %}>
<p>Inclui todos os lançamentos dos dois dias, mesmo após zerar o caixa.</p>
</div>
<button type="submit">Consultar relatório</button>
<button type="submit" name="formato" value="pdf" style="background:#225f43">📄 Baixar PDF do período</button>
</form>
<script>
(function () {
    const periodo = document.getElementById("periodo");
    const inicio = document.getElementById("data_inicio");
    const fim = document.getElementById("data_fim");
    function atualizar() {
        const personalizado = periodo.value === "personalizado";
        document.getElementById("filtro-referencia").hidden = personalizado;
        document.getElementById("filtro-personalizado").hidden = !personalizado;
        document.getElementById("data").required = !personalizado;
        inicio.required = fim.required = personalizado;
        fim.min = inicio.value;
    }
    periodo.addEventListener("change", atualizar);
    inicio.addEventListener("change", atualizar);
    atualizar();
})();
</script>

<p>
{{ inicio_local.strftime("%d/%m/%Y") }}
a {{ ultimo_dia.strftime("%d/%m/%Y") }}.
Horário do Piauí. Semana de segunda a domingo.
</p>

<div class="cards">
<div class="card">
<h3>Vendas no período</h3>
<div class="valor">{{ total|dinheiro }}</div>
<p>{{ vendas|length }} itens vendidos</p>
</div>
<div class="card">
<h3>Lucro bruto das vendas</h3>
<div class="valor">{{ lucro|dinheiro }}</div>
</div>
<div class="card">
<h3>Entradas de caixa no período</h3>
<div class="valor">{{ entradas|dinheiro }}</div>
</div>
<div class="card">
<h3>Saídas de caixa no período</h3>
<div class="valor">{{ saidas|dinheiro }}</div>
</div>
<div class="card">
<h3>Movimentação líquida do período</h3>
<div class="valor">{{ (entradas - saidas)|dinheiro }}</div>
</div>
<div class="card">
<h3>Fiado ainda pendente das vendas deste período</h3>
<div class="valor">{{ fiado_atual|dinheiro }}</div>
</div>
</div>

<p>
O fiado mostra a dívida atual, incluindo pagamentos posteriores.
Entradas de caixa incluem recebimentos de vendas antigas e
lançamentos manuais. Lucro bruto não desconta despesas.
Movimentação líquida não é o saldo acumulado do caixa.
</p>

{% if periodo in ("mensal", "personalizado") %}
<h2>📦 Resumo por produto</h2>
<p>
Esta tabela soma todas as vendas válidas do período por produto.
O lucro é o valor gravado em cada venda, preservando o histórico
mesmo quando o preço ou custo do produto muda depois.
</p>
<div class="tabela">
<table>
<thead>
<tr>
<th>Produto</th>
<th>Unidade</th>
<th>Quantidade vendida</th>
<th>Faturamento</th>
<th>Lucro bruto</th>
<th>Nº de vendas</th>
</tr>
</thead>
<tbody>
{% for item in produtos_agrupados %}
<tr>
<td><strong>{{ item.produto }}</strong></td>
<td>{{ item.unidade }}</td>
<td>{{ item.quantidade }}</td>
<td>{{ item.faturamento|dinheiro }}</td>
<td>{{ item.lucro|dinheiro }}</td>
<td>{{ item.vendas }}</td>
</tr>
{% else %}
<tr>
<td colspan="6">Nenhuma venda no período selecionado.</td>
</tr>
{% endfor %}
</tbody>
{% if produtos_agrupados %}
<tfoot>
<tr>
<th colspan="3">TOTAL DO PERÍODO</th>
<th>{{ total|dinheiro }}</th>
<th>{{ lucro|dinheiro }}</th>
<th>{{ vendas|length }}</th>
</tr>
</tfoot>
{% endif %}
</table>
</div>
{% endif %}

<h2>Vendas detalhadas</h2>
<div class="tabela">
<table>
<tr>
<th>Data</th>
<th>Cliente</th>
<th>Produto</th>
<th>Quantidade</th>
<th>Total</th>
<th>Lucro bruto</th>
<th>Tipo</th>
</tr>
{% for v in vendas %}
<tr>
<td>{{ (v.data|data_local).strftime("%d/%m/%Y %H:%M") }}</td>
<td>{{ v.cliente.nome if v.cliente else "Sem cliente" }}</td>
<td>{{ v.produto.nome }}</td>
<td>{{ v.quantidade }} {{ v.produto.unidade }}</td>
<td>{{ v.total|dinheiro }}</td>
<td>{{ v.lucro|dinheiro }}</td>
<td>{{ "Fiado" if v.tipo == "fiado" else "À vista" }}</td>
</tr>
{% else %}
<tr><td colspan="7">Nenhuma venda neste período.</td></tr>
{% endfor %}
</table>
</div>

<h2>Movimentações de caixa</h2>
<div class="tabela">
<table>
<tr><th>Data</th><th>Descrição</th><th>Tipo</th><th>Valor</th></tr>
{% for m in movimentos %}
<tr>
<td>{{ (m.data|data_local).strftime("%d/%m/%Y %H:%M") }}</td>
<td>{{ m.descricao }}</td>
<td>{{ "Entrada" if m.tipo == "entrada" else "Saída" }}</td>
<td>{{ m.valor|dinheiro }}</td>
</tr>
{% else %}
<tr><td colspan="4">Nenhuma movimentação neste período.</td></tr>
{% endfor %}
</table>
</div>
""",
        periodo=periodo,
        referencia=referencia.strftime("%Y-%m-%d"),
        data_inicio=data_inicio,
        data_fim=data_fim,
        inicio_local=inicio_local,
        ultimo_dia=fim_local - timedelta(days=1),
        vendas=vendas,
        movimentos=movimentos,
        total=total,
        lucro=lucro,
        entradas=entradas,
        saidas=saidas,
        fiado_atual=fiado_atual,
        produtos_agrupados=produtos_agrupados
    )



@app.route("/auditoria-financeira")
def auditoria_financeira():
    """Conciliação somente leitura; nunca corrige valores automaticamente."""
    if not logado():
        return redirect("/login")

    vendas_todas = Venda.query.order_by(Venda.id).all()
    exclusoes = {e.venda_id for e in ExclusaoVenda.query.all()}
    validas = [v for v in vendas_todas if v.id not in exclusoes]
    por_id = {v.id: v for v in vendas_todas}
    grupos = VendaGrupo.query.order_by(VendaGrupo.id).all()
    vinculos = VendaGrupoItem.query.order_by(VendaGrupoItem.id).all()
    caixa_todo = Caixa.query.order_by(Caixa.id).all()
    produtos = Produto.query.all()
    vinculos_por_grupo = {}
    for item in vinculos:
        vinculos_por_grupo.setdefault(item.grupo_id, []).append(item.venda_id)

    movimentos_por_compra = {}
    recebimentos_por_venda = {}
    for m in caixa_todo:
        descricao = m.descricao or ""
        if m.tipo == "entrada" and descricao.startswith("Compra #"):
            try:
                compra_id = int(descricao.split(" - ", 1)[0][8:])
                movimentos_por_compra.setdefault(compra_id, []).append(m)
            except (ValueError, IndexError):
                pass
        if m.tipo == "entrada" and descricao.startswith("Pagamento de fiado - Venda #"):
            try:
                venda_id = int(descricao.rsplit("#", 1)[1])
                recebimentos_por_venda.setdefault(venda_id, []).append(m)
            except (ValueError, IndexError):
                pass

    problemas = []
    def apontar(nivel, origem, mensagem):
        problemas.append({"nivel": nivel, "origem": origem, "mensagem": mensagem})

    for v in vendas_todas:
        qtd = Decimal(v.quantidade or 0)
        preco = Decimal(v.preco_unitario or 0)
        custo = Decimal(v.custo_unitario or 0)
        total = Decimal(v.total or 0)
        lucro = Decimal(v.lucro or 0)
        pago = Decimal(v.pago or 0)
        if qtd <= 0 or total < 0 or pago < 0 or pago > total:
            apontar("Alerta", "Venda #" + str(v.id),
                    "Quantidade, total ou pagamento fora dos limites.")
        if abs(total - dinheiro_decimal(qtd * preco)) > CENTAVO:
            apontar("Alerta", "Venda #" + str(v.id),
                    "Total gravado diferente da quantidade × preço unitário.")
        lucro_esperado = total - dinheiro_decimal(qtd * custo)
        if abs(lucro - lucro_esperado) > CENTAVO:
            apontar("Alerta", "Venda #" + str(v.id),
                    "Lucro gravado diferente de total menos custo da mercadoria.")

    for m in caixa_todo:
        valor = Decimal(m.valor or 0)
        if m.tipo not in ("entrada", "saida") or not valor_monetario_valido(valor):
            apontar("Alerta", "Caixa #" + str(m.id),
                    "Movimento com tipo ou valor inconsistente.")

    for p in produtos:
        if Decimal(p.estoque or 0) < 0:
            apontar("Alerta", "Produto #" + str(p.id),
                    "Estoque negativo: " + p.nome)

    for grupo in grupos:
        ids = vinculos_por_grupo.get(grupo.id, [])
        itens = [por_id[i] for i in ids if i in por_id]
        if len(itens) != len(ids) or not itens:
            apontar("Alerta", "Compra #" + str(grupo.id),
                    "Compra sem todos os itens associados.")
        total_itens = sum((Decimal(v.total or 0) for v in itens), Decimal("0"))
        if abs(total_itens - Decimal(grupo.total or 0)) > CENTAVO:
            apontar("Alerta", "Compra #" + str(grupo.id),
                    "Total do pedido diferente da soma de seus itens.")
        lancamentos = movimentos_por_compra.get(grupo.id, [])
        pago_inicial = Decimal(grupo.pago or 0)
        valor_caixa = sum((Decimal(m.valor or 0) for m in lancamentos), Decimal("0"))
        if abs(valor_caixa - pago_inicial) > CENTAVO:
            apontar("Alerta", "Compra #" + str(grupo.id),
                    "Entrada inicial no caixa diferente do pagamento da compra.")
        if len(lancamentos) > 1:
            apontar("Revisar", "Compra #" + str(grupo.id),
                    "Mais de uma entrada de caixa associada à mesma compra.")
        recebido_posterior = sum(
            (Decimal(m.valor or 0)
             for v in itens for m in recebimentos_por_venda.get(v.id, [])),
            Decimal("0")
        )
        pago_atual = sum((Decimal(v.pago or 0) for v in itens), Decimal("0"))
        if abs(pago_atual - pago_inicial - recebido_posterior) > CENTAVO:
            apontar("Alerta", "Compra #" + str(grupo.id),
                    "Total pago nos itens não concilia com pagamentos e recebimentos.")

    for venda_id, registros in recebimentos_por_venda.items():
        if venda_id not in por_id:
            apontar("Alerta", "Fiado #" + str(venda_id),
                    "Recebimento no caixa sem venda correspondente.")

    # Possível reenvio do mesmo pedido. Apenas sinaliza; nunca exclui.
    por_cliente_valor = {}
    for grupo in grupos:
        if grupo.cliente_id is not None and Decimal(grupo.total or 0) > 0:
            por_cliente_valor.setdefault(
                (grupo.cliente_id, Decimal(grupo.total or 0)), []
            ).append(grupo)
    for similares in por_cliente_valor.values():
        similares.sort(key=lambda g: (g.data, g.id))
        for anterior, seguinte in zip(similares, similares[1:]):
            if timedelta(0) <= seguinte.data - anterior.data <= timedelta(minutes=2):
                apontar(
                    "Revisar", "Compras #" + str(anterior.id) + " e #" + str(seguinte.id),
                    "Mesmo cliente e valor em menos de 2 minutos; confirme se foram compras distintas."
                )

    fechamento = db.session.query(
        db.func.max(FechamentoCaixa.ultima_venda_id),
        db.func.max(FechamentoCaixa.ultimo_movimento_id)
    ).one()
    ultima_venda, ultimo_movimento = fechamento
    vendas_atuais_lista = [v for v in validas if v.id > (ultima_venda or 0)]
    movimentos_atuais_lista = [m for m in caixa_todo if m.id > (ultimo_movimento or 0)]
    vendas_periodo = sum((Decimal(v.total or 0) for v in vendas_atuais_lista), Decimal("0"))
    lucro_periodo = sum((Decimal(v.lucro or 0) for v in vendas_atuais_lista), Decimal("0"))
    entradas = sum((Decimal(m.valor or 0) for m in movimentos_atuais_lista
                    if m.tipo == "entrada"), Decimal("0"))
    saidas = sum((Decimal(m.valor or 0) for m in movimentos_atuais_lista
                  if m.tipo == "saida"), Decimal("0"))
    fiado_geral = sum(
        (v.saldo_devedor for v in validas if v.tipo == "fiado"),
        Decimal("0")
    )
    despesas = sum(
        (Decimal(m.valor or 0) for m in movimentos_atuais_lista
         if eh_despesa_da_loja(m)), Decimal("0")
    )
    problemas.sort(key=lambda p: (p["nivel"] != "Alerta", p["origem"]))
    return pagina("""
<h1>🔎 Auditoria financeira</h1>
<p><strong>Conferência de leitura, sem alterar valores.</strong>
Os cálculos abaixo usam os registros do banco e o último zeramento do caixa.
A ausência de alertas não substitui a contagem física do dinheiro e do estoque.</p>
<div class="cards">
<div class="card"><h3>Vendas registradas (caixa atual)</h3><div class="valor">{{ vendas_periodo|dinheiro }}</div></div>
<div class="card"><h3>Lucro bruto (caixa atual)</h3><div class="valor">{{ lucro_periodo|dinheiro }}</div></div>
<div class="card"><h3>Entradas de caixa</h3><div class="valor">{{ entradas|dinheiro }}</div></div>
<div class="card"><h3>Saídas de caixa</h3><div class="valor despesa">{{ saidas|dinheiro }}</div></div>
<div class="card"><h3>Saldo por movimentações</h3><div class="valor">{{ (entradas-saidas)|dinheiro }}</div></div>
<div class="card"><h3>Fiado pendente (todas as datas)</h3><div class="valor">{{ fiado_geral|dinheiro }}</div></div>
<div class="card"><h3>Despesas da loja (caixa atual)</h3><div class="valor despesa">{{ despesas|dinheiro }}</div></div>
<div class="card"><h3>Inconsistências / verificações</h3><div class="valor">{{ problemas|length }}</div></div>
</div>
<h2>Conciliação e pontos para conferir</h2>
<p>
O caixa é a soma das entradas menos as saídas, desde o último zeramento.
O lucro apresentado é <strong>bruto</strong> e não equivale ao saldo em dinheiro.
As compras de estoque ficam no caixa, mas seu custo também compõe o lucro
bruto das mercadorias vendidas: não desconte essas compras duas vezes.
</p>
<div class="tabela">
<table>
<tr><th>Situação</th><th>Registro</th><th>O que verificar</th></tr>
{% for problema in problemas[:100] %}
<tr>
<td>{{ problema.nivel }}</td>
<td>{{ problema.origem }}</td>
<td>{{ problema.mensagem }}</td>
</tr>
{% else %}
<tr><td colspan="3">Nenhuma inconsistência detectada pelas verificações automáticas.</td></tr>
{% endfor %}
</table>
</div>
{% if problemas|length > 100 %}
<p>Exibindo 100 de {{ problemas|length }} apontamentos.</p>
{% endif %}
<p class="aviso">
<strong>Atenção:</strong> possíveis compras duplicadas são apenas suspeitas.
Não apague registros sem conferir comprovantes, pagamento e entrega de mercadorias.
O custo médio passa a valer nas novas entradas; vendas antigas mantêm o custo
que estava gravado na época.
</p>
<p><a href="/relatorios">Conferir relatórios detalhados</a> · <a href="/caixa">Ver caixa</a></p>
""",
        vendas_periodo=vendas_periodo, lucro_periodo=lucro_periodo,
        entradas=entradas, saidas=saidas, fiado_geral=fiado_geral,
        despesas=despesas, problemas=problemas)


@app.route("/analise-negocio")
def analise_negocio():
    if not logado():
        return redirect("/login")

    agora = datetime.now(FUSO_LOJA)
    mes_texto = request.args.get("mes", agora.strftime("%Y-%m"))

    try:
        inicio_local = datetime.strptime(
            mes_texto, "%Y-%m"
        ).replace(tzinfo=FUSO_LOJA)
    except ValueError:
        flash("Mês inválido.", "erro")
        return redirect("/analise-negocio")

    if inicio_local.month == 12:
        fim_local = inicio_local.replace(
            year=inicio_local.year + 1,
            month=1
        )
    else:
        fim_local = inicio_local.replace(
            month=inicio_local.month + 1
        )

    inicio = inicio_local.astimezone(
        timezone.utc
    ).replace(tzinfo=None)
    fim = fim_local.astimezone(
        timezone.utc
    ).replace(tzinfo=None)

    vendas = vendas_validas().filter(
        Venda.data >= inicio,
        Venda.data < fim
    ).order_by(Venda.data.asc(), Venda.id.asc()).all()

    total_vendido = sum(
        (Decimal(v.total or 0) for v in vendas),
        Decimal("0")
    )
    lucro_total = sum(
        (Decimal(v.lucro or 0) for v in vendas),
        Decimal("0")
    )

    por_produto = {}
    por_cliente = {}
    por_dia = {}
    vinculos_mes = VendaGrupoItem.query.filter(
        VendaGrupoItem.venda_id.in_([v.id for v in vendas] or [-1])
    ).all()
    compra_por_venda = {i.venda_id: i.grupo_id for i in vinculos_mes}
    compras_ja_contadas = set()

    for venda in vendas:
        produto = venda.produto
        registro = por_produto.setdefault(
            venda.produto_id,
            {
                "nome": produto.nome,
                "unidade": produto.unidade,
                "quantidade": Decimal("0"),
                "total": Decimal("0"),
                "lucro": Decimal("0")
            }
        )
        registro["quantidade"] += Decimal(venda.quantidade or 0)
        registro["total"] += Decimal(venda.total or 0)
        registro["lucro"] += Decimal(venda.lucro or 0)

        if venda.cliente_id and venda.cliente:
            cliente = por_cliente.setdefault(
                venda.cliente_id,
                {
                    "nome": venda.cliente.nome,
                    "total": Decimal("0"),
                    "compras": 0
                }
            )
            cliente["total"] += Decimal(venda.total or 0)
            chave_compra = (
                ("grupo", compra_por_venda[venda.id])
                if venda.id in compra_por_venda else ("avulsa", venda.id)
            )
            if chave_compra not in compras_ja_contadas:
                compras_ja_contadas.add(chave_compra)
                cliente["compras"] += 1

        dia = data_local(venda.data).date()
        por_dia[dia] = por_dia.get(
            dia, Decimal("0")
        ) + Decimal(venda.total or 0)

    mais_vendido = max(
        por_produto.values(),
        key=lambda item: item["quantidade"],
        default=None
    )
    mais_lucrativo = max(
        por_produto.values(),
        key=lambda item: item["lucro"],
        default=None
    )
    melhor_cliente = max(
        por_cliente.values(),
        key=lambda item: item["total"],
        default=None
    )

    if por_dia:
        melhor_dia_data, melhor_dia_total = max(
            por_dia.items(),
            key=lambda item: item[1]
        )
        melhor_dia = {
            "data": melhor_dia_data,
            "total": melhor_dia_total
        }
    else:
        melhor_dia = None

    ids_vendidos = set(por_produto)
    produtos_parados = [
        produto
        for produto in Produto.query.order_by(Produto.nome).all()
        if Decimal(produto.estoque or 0) > 0
        and produto.id not in ids_vendidos
    ]

    produtos_tabela = sorted(
        por_produto.values(),
        key=lambda item: (
            item["total"],
            item["quantidade"]
        ),
        reverse=True
    )

    top_produtos = produtos_tabela[:8]
    maior_faturamento_produto = max(
        (item["total"] for item in top_produtos),
        default=Decimal("0")
    )
    for item in top_produtos:
        item["percentual"] = (
            float(item["total"] / maior_faturamento_produto * 100)
            if maior_faturamento_produto > 0 else 0
        )

    vendas_por_dia = [
        {"data": dia, "total": total}
        for dia, total in sorted(por_dia.items())
    ]
    maior_dia = max(
        (item["total"] for item in vendas_por_dia),
        default=Decimal("0")
    )
    for item in vendas_por_dia:
        item["percentual"] = (
            float(item["total"] / maior_dia * 100)
            if maior_dia > 0 else 0
        )

    return pagina("""
<style>
.indicador-destaque{
    font-size:16px;
    margin-top:6px;
    line-height:1.45
}
.grafico-barras{
    background:white;
    border-radius:10px;
    padding:16px;
    box-shadow:0 2px 7px #00000015;
    margin-bottom:20px
}
.linha-grafico{
    display:grid;
    grid-template-columns:minmax(120px,220px) 1fr minmax(90px,130px);
    gap:10px;
    align-items:center;
    margin:10px 0
}
.barra-fundo{
    height:18px;
    background:#e9eceb;
    border-radius:999px;
    overflow:hidden
}
.barra-valor{
    height:100%;
    min-width:2px;
    background:#176b3a;
    border-radius:999px
}
.numero-grafico{text-align:right;font-weight:bold}
@media(max-width:650px){
    .linha-grafico{
        grid-template-columns:1fr;
        gap:4px
    }
    .numero-grafico{text-align:left}
}
</style>

<h1>📊 Análise do Negócio</h1>
<p>
Indicadores calculados diretamente das vendas válidas do banco de dados.
Vendas excluídas não entram nos resultados.
</p>

<form method="get" class="formulario">
<label for="mes">Mês analisado</label>
<input id="mes" name="mes" type="month" value="{{ mes_texto }}" required>
<button>Atualizar análise</button>
</form>

<h2>{{ inicio_local.strftime("%m/%Y") }}</h2>

<div class="cards">
<div class="card">
<h3>🧾 Total vendido</h3>
<div class="valor">{{ total_vendido|dinheiro }}</div>
<p>{{ vendas|length }} itens vendidos</p>
</div>

<div class="card">
<h3>📈 Lucro bruto</h3>
<div class="valor">{{ lucro_total|dinheiro }}</div>
<p>Usa o lucro gravado no momento de cada venda.</p>
</div>

<div class="card">
<h3>🥇 Produto mais vendido</h3>
{% if mais_vendido %}
<div class="valor">{{ mais_vendido.nome }}</div>
<p class="indicador-destaque">
{{ mais_vendido.quantidade }} {{ mais_vendido.unidade }}
</p>
{% else %}
<p>Sem vendas no período.</p>
{% endif %}
</div>

<div class="card">
<h3>💰 Produto que mais deu lucro</h3>
{% if mais_lucrativo %}
<div class="valor">{{ mais_lucrativo.nome }}</div>
<p class="indicador-destaque">
{{ mais_lucrativo.lucro|dinheiro }}
</p>
{% else %}
<p>Sem vendas no período.</p>
{% endif %}
</div>

<div class="card">
<h3>📅 Melhor dia de vendas</h3>
{% if melhor_dia %}
<div class="valor">{{ melhor_dia.total|dinheiro }}</div>
<p class="indicador-destaque">
{{ melhor_dia.data.strftime("%d/%m/%Y") }}
</p>
{% else %}
<p>Sem vendas no período.</p>
{% endif %}
</div>

<div class="card">
<h3>👤 Cliente que mais comprou</h3>
{% if melhor_cliente %}
<div class="valor">{{ melhor_cliente.nome }}</div>
<p class="indicador-destaque">
{{ melhor_cliente.total|dinheiro }} em
{{ melhor_cliente.compras }} compra(s)
</p>
{% else %}
<p>Nenhuma venda com cliente identificado.</p>
{% endif %}
</div>
</div>

<h2>Faturamento por produto</h2>
<div class="grafico-barras">
{% for item in top_produtos %}
<div class="linha-grafico">
<div>{{ item.nome }}</div>
<div class="barra-fundo" title="{{ item.total|dinheiro }}">
<div class="barra-valor" style="width:{{ '%.2f'|format(item.percentual) }}%"></div>
</div>
<div class="numero-grafico">{{ item.total|dinheiro }}</div>
</div>
{% else %}
<p>Nenhuma venda no período.</p>
{% endfor %}
</div>

<h2>Vendas por dia</h2>
<div class="grafico-barras">
{% for item in vendas_por_dia %}
<div class="linha-grafico">
<div>{{ item.data.strftime("%d/%m") }}</div>
<div class="barra-fundo" title="{{ item.total|dinheiro }}">
<div class="barra-valor" style="width:{{ '%.2f'|format(item.percentual) }}%"></div>
</div>
<div class="numero-grafico">{{ item.total|dinheiro }}</div>
</div>
{% else %}
<p>Nenhuma venda no período.</p>
{% endfor %}
</div>

<h2>📋 Tabela completa por produto</h2>
<p>
Resumo de todos os produtos vendidos no mês escolhido.
</p>
<div class="tabela">
<table>
<thead>
<tr>
<th>Produto</th>
<th>Quantidade vendida</th>
<th>Faturamento</th>
<th>Lucro bruto</th>
</tr>
</thead>
<tbody>
{% for item in produtos_tabela %}
<tr>
<td><strong>{{ item.nome }}</strong></td>
<td>{{ item.quantidade }} {{ item.unidade }}</td>
<td>{{ item.total|dinheiro }}</td>
<td>{{ item.lucro|dinheiro }}</td>
</tr>
{% else %}
<tr>
<td colspan="4">Nenhuma venda no mês selecionado.</td>
</tr>
{% endfor %}
</tbody>
{% if produtos_tabela %}
<tfoot>
<tr>
<th colspan="2">TOTAL DO MÊS</th>
<th>{{ total_vendido|dinheiro }}</th>
<th>{{ lucro_total|dinheiro }}</th>
</tr>
</tfoot>
{% endif %}
</table>
</div>

<p>
<a href="{{ url_for('relatorios', periodo='mensal', data=inicio_local.strftime('%Y-%m-%d')) }}">
Ver relatório mensal detalhado
</a>
&nbsp;•&nbsp;
<a href="{{ url_for('relatorios', periodo='personalizado') }}">
Abrir relatório personalizado
</a>
</p>

<h2>📦 Produtos parados no estoque</h2>
<p>
Produtos com estoque acima de zero e nenhuma venda registrada no mês escolhido.
</p>
<div class="tabela">
<table>
<tr>
<th>Produto</th>
<th>Estoque atual</th>
<th>Situação</th>
</tr>
{% for produto in produtos_parados %}
<tr>
<td>{{ produto.nome }}</td>
<td>{{ produto.estoque }} {{ produto.unidade }}</td>
<td>Sem venda no mês</td>
</tr>
{% else %}
<tr>
<td colspan="3">Nenhum produto com estoque ficou sem vender neste mês.</td>
</tr>
{% endfor %}
</table>
</div>

<p class="aviso">
<strong>Como usar esses dados:</strong>
produtos com alto faturamento e giro merecem atenção no estoque;
produtos parados devem ser avaliados antes de novas compras;
o lucro usa o custo registrado em cada venda, preservando o histórico mesmo
quando o preço atual do produto muda.
</p>
""",
        mes_texto=mes_texto,
        inicio_local=inicio_local,
        vendas=vendas,
        total_vendido=total_vendido,
        lucro_total=lucro_total,
        mais_vendido=mais_vendido,
        mais_lucrativo=mais_lucrativo,
        melhor_cliente=melhor_cliente,
        melhor_dia=melhor_dia,
        produtos_parados=produtos_parados,
        produtos_tabela=produtos_tabela,
        top_produtos=top_produtos,
        vendas_por_dia=vendas_por_dia
    )


PRODUTOS_CATALOGO = [
    ("Soja", "Grãos e farelos", "🌱"),
    ("Milho", "Grãos e farelos", "🌽"),
    ("Xerém de milho", "Grãos e farelos", "🌽"),
    ("Feijão branco", "Grãos e farelos", "🫘"),
    ("Feijão vermelho", "Grãos e farelos", "🫘"),
    ("Farelo de trigo", "Grãos e farelos", "🌾"),
    ("Torta de algodão", "Grãos e farelos", "🌿"),
    ("Cuim de arroz", "Grãos e farelos", "🌾"),
    ("Ração de postura", "Aves", "🐔"),
    ("Ração inicial para galinhas", "Aves", "🐣"),
    ("Ração de crescimento para suínos", "Suínos", "🐷"),
    ("Ração de terminação para suínos", "Suínos", "🐷"),
    ("Núcleo para suínos", "Suínos", "🐷"),
    ("Núcleo para bovinos", "Bovinos", "🐮"),
    ("Ração para cães", "Cães e gatos", "🐶"),
    ("Ração para gatos", "Cães e gatos", "🐱"),
    ("Ração para peixes", "Peixes", "🐟")
]


def link_whatsapp(produto=None):
    if produto:
        mensagem = (
            "Olá! Gostaria de consultar preço, embalagem e "
            "disponibilidade de " + produto +
            " na Casa das Rações Reis."
        )
    else:
        mensagem = (
            "Olá! Gostaria de informações sobre os produtos "
            "da Casa das Rações Reis."
        )

    return (
        "https://wa.me/5589981273202?"
        + urlencode({"text": mensagem})
    )


CATALOGO_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catálogo | Casa das Rações Reis</title>
<meta name="description"
      content="Catálogo da Casa das Rações Reis em Campo Grande do Piauí. Rações, grãos, farelos e núcleos. Consulte pelo WhatsApp.">
<style>
*{box-sizing:border-box}
body{
    margin:0;
    font-family:Arial,sans-serif;
    background:#f5f6ef;
    color:#19382b
}
a{color:inherit}
.topo{background:#103d26;color:white;padding:20px}
.limite{max-width:1100px;margin:auto}
.barra{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:12px;
    flex-wrap:wrap
}
.marca{font-size:20px;font-weight:bold;text-decoration:none}
.interno{
    font-size:14px;
    text-decoration:none;
    border:1px solid #ffffff66;
    border-radius:8px;
    padding:10px 14px
}
.hero{
    background:linear-gradient(135deg,#103d26,#20623d);
    color:white;
    padding:44px 20px
}
.hero h1{
    font-size:clamp(30px,6vw,54px);
    margin:12px 0;
    max-width:720px
}
.hero p{font-size:18px;line-height:1.6;max-width:650px}
.selo{color:#ffe08a;font-weight:bold}
.botao{
    display:inline-block;
    background:#ffcf45;
    color:#19382b;
    padding:14px 20px;
    border-radius:10px;
    text-decoration:none;
    font-weight:bold
}
main{padding:28px 20px}
.contatos{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
    gap:15px;
    margin-bottom:35px
}
.contato,.produto{
    background:white;
    border:1px solid #e0e6dc;
    border-radius:15px;
    padding:22px
}
.contato h2{font-size:16px;margin:0 0 12px}
.contato p{line-height:1.6;margin:0}
.busca{
    display:flex;
    gap:12px;
    flex-wrap:wrap;
    background:white;
    padding:18px;
    border-radius:12px;
    margin:20px 0
}
.busca label{display:block;font-size:14px;margin-bottom:6px}
.busca div{flex:1;min-width:180px}
input,select,button{
    font:inherit;
    border-radius:8px;
    padding:12px;
    border:1px solid #b9cbbd
}
input,select{width:100%}
button{
    background:#176b3a;
    color:white;
    cursor:pointer;
    align-self:end
}
.grade{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
    gap:16px
}
.icone{font-size:42px;margin-bottom:14px}
.categoria{font-size:13px;color:#55705c}
.produto h3{font-size:20px;margin:10px 0;line-height:1.3}
.produto p{color:#55705c;font-size:14px;line-height:1.6}
.produto .botao{
    background:#176b3a;
    color:white;
    width:100%;
    text-align:center;
    font-size:14px
}
.banner{margin-top:35px}
.banner img{
    display:block;
    width:100%;
    max-width:650px;
    height:auto;
    margin:auto;
    border-radius:15px
}
footer{
    background:#103d26;
    color:white;
    padding:25px 20px;
    line-height:1.7;
    margin-top:30px
}
.vazio{padding:25px;background:white;border-radius:12px}
</style>
</head>
<body>

<header class="topo">
<div class="limite barra">
<a class="marca" href="/catalogo">🌾 Casa das Rações Reis</a>
<a class="interno"
   href="{{ '/' if session.get('logado') else '/login' }}">
Área da loja
</a>
</div>
</header>

<section class="hero">
<div class="limite">
<div class="selo">CAMPO GRANDE DO PIAUÍ • PI</div>
<h1>Qualidade e compromisso com o homem do campo</h1>
<p>
Conheça nossas rações, grãos, farelos e núcleos.
Fale com a loja para consultar preços e disponibilidade.
</p>
<a class="botao" href="{{ whatsapp }}"
   target="_blank" rel="noopener noreferrer">
Falar pelo WhatsApp
</a>
</div>
</section>

<main class="limite">
<section class="contatos" aria-label="Contato e atendimento">
<div class="contato">
<h2>📱 WhatsApp</h2>
<p>
<a href="{{ whatsapp }}" target="_blank" rel="noopener noreferrer">
(89) 98127-3202
</a>
</p>
</div>

<div class="contato">
<h2>📍 Endereço</h2>
<p>
Rua Pedro Carvalho Gomes, nº 73<br>
Campo Grande do Piauí – PI
</p>
<a href="{{ mapa }}" target="_blank" rel="noopener noreferrer">
Ver endereço no mapa
</a>
</div>

<div class="contato">
<h2>🕒 Atendimento</h2>
<p>
Das 07h às 17h<br>
Consulte os dias de funcionamento pelo WhatsApp.
</p>
</div>
</section>

<h2>Nosso catálogo</h2>
<p>
Consulte preços, tamanhos das embalagens e disponibilidade com a loja.
</p>

<form class="busca" method="get" action="/catalogo">
<div>
<label for="q">Buscar produto</label>
<input id="q" name="q" value="{{ busca }}"
       placeholder="Ex.: milho, postura, suínos" maxlength="150">
</div>

<div>
<label for="categoria">Categoria</label>
<select id="categoria" name="categoria">
<option value="">Todas as categorias</option>
{% for categoria in categorias %}
<option value="{{ categoria }}"
        {% if filtro == categoria %}selected{% endif %}>
{{ categoria }}
</option>
{% endfor %}
</select>
</div>

<button type="submit">Buscar</button>
</form>

<div class="grade">
{% for produto in produtos %}
<article class="produto">
<div class="icone" aria-hidden="true">{{ produto.icone }}</div>
<span class="categoria">{{ produto.categoria }}</span>
<h3>{{ produto.nome }}</h3>
<p>Preço e embalagem sob consulta.</p>
<a class="botao" href="{{ produto.whatsapp }}"
   target="_blank" rel="noopener noreferrer">
Consultar pelo WhatsApp
</a>
</article>
{% else %}
<p class="vazio">
Nenhum produto encontrado.
<a href="/catalogo">Ver todo o catálogo</a>
</p>
{% endfor %}
</div>

{% if tem_banner %}
<section class="banner" aria-label="Apresentação da loja">
<h2>Casa das Rações Reis</h2>
<img src="{{ url_for('static', filename='catalogo-loja.png') }}"
     alt="Apresentação da Casa das Rações Reis e suas linhas de produtos"
     loading="lazy">
</section>
{% endif %}
</main>

<footer>
<div class="limite">
<strong>Casa das Rações Reis</strong><br>
Rua Pedro Carvalho Gomes, nº 73 — Campo Grande do Piauí – PI<br>
WhatsApp: (89) 98127-3202 • Atendimento das 07h às 17h
</div>
</footer>
</body>
</html>
"""


@app.route("/catalogo")
def catalogo_publico():
    busca = request.args.get("q", "").strip()[:150]
    filtro = request.args.get("categoria", "")
    categorias = sorted({p[1] for p in PRODUTOS_CATALOGO})

    produtos = [
        {
            "nome": nome,
            "categoria": categoria,
            "icone": icone,
            "whatsapp": link_whatsapp(nome)
        }
        for nome, categoria, icone in PRODUTOS_CATALOGO
        if (
            not busca or busca.casefold() in nome.casefold()
        ) and (
            not filtro or categoria == filtro
        )
    ]

    mapa = "https://www.google.com/maps/search/?" + urlencode({
        "api": "1",
        "query": (
            "Rua Pedro Carvalho Gomes, 73, "
            "Campo Grande do Piauí, PI"
        )
    })

    tem_banner = bool(
        app.static_folder
        and os.path.isfile(
            os.path.join(app.static_folder, "catalogo-loja.png")
        )
    )

    return render_template_string(
        CATALOGO_HTML,
        produtos=produtos,
        categorias=categorias,
        busca=busca,
        filtro=filtro,
        whatsapp=link_whatsapp(),
        mapa=mapa,
        tem_banner=tem_banner
    )


def token_banners():
    if "csrf_banners" not in session:
        session["csrf_banners"] = secrets.token_urlsafe(32)
    return session["csrf_banners"]


def ler_foto_banner(arquivo):
    dados = arquivo.read(5 * 1024 * 1024 + 1)

    if not dados or len(dados) > 5 * 1024 * 1024:
        raise ValueError("Escolha uma foto de até 5 MB.")

    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif dados.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Use uma foto JPG, PNG ou WebP.")

    return dados, mime


ADMIN_BANNERS_HTML = """
<h1>Banners da loja</h1>
<p>
Envie fotos pelo celular e escreva o slogan de cada banner.
Os ativos aparecem no catálogo, do menor número de ordem para o maior.
</p>

<div class="formulario">
<h2>Adicionar banner</h2>
<form method="post" enctype="multipart/form-data">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="csrf_banners" value="{{ token }}">
<input type="hidden" name="acao" value="criar">

<label for="foto_nova">Foto (JPG, PNG ou WebP, até 5 MB)</label>
<input id="foto_nova" type="file" name="foto"
       accept="image/jpeg,image/png,image/webp" required>

<label for="slogan_novo">Slogan</label>
<input id="slogan_novo" name="slogan" maxlength="180"
       placeholder="Ex.: Qualidade e compromisso com o homem do campo"
       required>

<label for="ordem_nova">Ordem de exibição</label>
<input id="ordem_nova" name="ordem" type="number"
       min="0" max="9999" value="{{ proxima_ordem }}" required>

<label>
<input style="width:auto" type="checkbox" name="ativo" checked>
Mostrar no catálogo
</label>

<button>Salvar novo banner</button>
</form>
</div>

<p>
<a href="/catalogo" target="_blank" rel="noopener">
Ver catálogo público
</a>
</p>

<h2>Banners cadastrados</h2>
{% for banner in banners %}
<div class="formulario" style="margin-bottom:20px">
<img src="{{ url_for('imagem_banner', banner_id=banner.id) }}"
     alt="{{ banner.slogan }}"
     style="display:block;width:100%;max-height:300px;object-fit:contain;background:#103d26;border-radius:8px">

<p>
<strong>{{ "Ativo" if banner.ativo else "Oculto" }}</strong>
</p>

<form method="post" enctype="multipart/form-data">
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="csrf_banners" value="{{ token }}">
<input type="hidden" name="acao" value="editar">
<input type="hidden" name="banner_id" value="{{ banner.id }}">

<label for="slogan_{{ banner.id }}">Slogan</label>
<input id="slogan_{{ banner.id }}" name="slogan" maxlength="180"
       value="{{ banner.slogan }}" required>

<label for="ordem_{{ banner.id }}">Ordem de exibição</label>
<input id="ordem_{{ banner.id }}" name="ordem" type="number"
       min="0" max="9999" value="{{ banner.ordem }}" required>

<label for="foto_{{ banner.id }}">Trocar foto (opcional)</label>
<input id="foto_{{ banner.id }}" type="file" name="foto"
       accept="image/jpeg,image/png,image/webp">

<label>
<input style="width:auto" type="checkbox" name="ativo"
       {% if banner.ativo %}checked{% endif %}>
Mostrar no catálogo
</label>

<button>Salvar alterações</button>
</form>
</div>
{% else %}
<p>Nenhum banner cadastrado. Adicione a primeira foto acima.</p>
{% endfor %}
"""


@app.route("/banners", methods=["GET", "POST"])
def administrar_banners():
    if not logado():
        return redirect("/login")

    if request.method == "POST":
        if (
            request.content_length
            and request.content_length > 6 * 1024 * 1024
        ):
            flash(
                "Arquivo muito grande. Use uma foto de até 5 MB.",
                "erro"
            )
            return redirect("/banners")

        recebido = request.form.get("csrf_banners", "")
        esperado = session.get("csrf_banners", "")

        if not esperado or not hmac.compare_digest(recebido, esperado):
            flash(
                "A sessão do formulário expirou. Tente novamente.",
                "erro"
            )
            return redirect("/banners")

        try:
            slogan = request.form.get("slogan", "").strip()

            if not slogan or len(slogan) > 180:
                raise ValueError(
                    "Escreva um slogan de até 180 caracteres."
                )

            try:
                ordem = int(request.form.get("ordem", ""))
            except ValueError:
                raise ValueError(
                    "Informe um número inteiro para a ordem."
                )

            if not 0 <= ordem <= 9999:
                raise ValueError(
                    "A ordem deve ficar entre 0 e 9999."
                )

            acao = request.form.get("acao")

            if acao == "criar":
                banner = BannerLoja()
            elif acao == "editar":
                try:
                    banner_id = int(
                        request.form.get("banner_id", "")
                    )
                except ValueError:
                    raise ValueError("Banner inválido.")

                banner = db.session.get(BannerLoja, banner_id)

                if banner is None:
                    raise ValueError("Banner não encontrado.")
            else:
                raise ValueError("Ação inválida.")

            arquivo = request.files.get("foto")

            if arquivo and arquivo.filename:
                dados, mime = ler_foto_banner(arquivo)
            elif acao == "criar":
                raise ValueError(
                    "Escolha a foto do novo banner."
                )
            else:
                dados = mime = None

            if dados is not None:
                banner.imagem = dados
                banner.mime = mime

            banner.slogan = slogan
            banner.ordem = ordem
            banner.ativo = request.form.get("ativo") == "on"

            db.session.add(banner)
            db.session.commit()
            flash("Banner salvo com sucesso.", "sucesso")

        except ValueError as erro:
            db.session.rollback()
            flash(str(erro), "erro")

        return redirect("/banners")

    banners = db.session.query(
        BannerLoja.id,
        BannerLoja.slogan,
        BannerLoja.ordem,
        BannerLoja.ativo
    ).order_by(BannerLoja.ordem, BannerLoja.id).all()

    proxima_ordem = min(
        9999,
        max((b.ordem for b in banners), default=0) + 1
    )

    return pagina(
        ADMIN_BANNERS_HTML,
        banners=banners,
        proxima_ordem=proxima_ordem,
        token=token_banners()
    )


@app.route("/banner-imagem/<int:banner_id>")
def imagem_banner(banner_id):
    banner = db.session.get(BannerLoja, banner_id)

    if banner is None or (not banner.ativo and not logado()):
        return Response(status=404)

    resposta = Response(
        banner.imagem,
        content_type=banner.mime
    )
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.route("/banner-fixo/<int:indice>")
def banner_fixo(indice):
    arquivos = {1: "banner-produtos.png", 2: "banner-fachada.png"}
    if indice not in arquivos:
        return "Banner não encontrado", 404
    return send_from_directory(app.root_path, arquivos[indice])


@app.context_processor
def banners_do_catalogo():
    if (
        request.endpoint == "catalogo_publico"
        or (
            request.endpoint == "painel"
            and not logado()
        )
    ):
        banners = db.session.query(
            BannerLoja.id,
            BannerLoja.slogan
        ).filter(
            BannerLoja.ativo.is_(True)
        ).order_by(
            BannerLoja.ordem,
            BannerLoja.id
        ).all()

        return {"banners_publicos": banners}

    return {"banners_publicos": []}


CARROSSEL_HTML = """
{% set total_banners = banners_publicos|length if banners_publicos else 2 %}
<style>
.carrossel-loja{
    background:#103d26;
    color:white;
    padding:22px 20px
}
.carrossel-loja .limite{position:relative}
.banner-slide[hidden]{display:none}
.banner-slide{margin:0;animation:entrada-banner .55s ease-out}
@keyframes entrada-banner{
    from{opacity:0;transform:translateY(12px)}
    to{opacity:1;transform:translateY(0)}
}
.banner-campanha{
    display:flex;align-items:center;justify-content:space-between;
    gap:24px;min-height:280px;padding:36px;
    border:1px solid #ffffff25;border-radius:18px;
    background:radial-gradient(circle at 90% 20%,#ffcf4525,transparent 45%),
               linear-gradient(120deg,#092719,#21613b)
}
.banner-campanha h2{font-size:clamp(27px,5vw,46px);margin:12px 0;max-width:650px}
.banner-campanha p{font-size:18px;line-height:1.6;max-width:600px;margin:0 0 20px}
.banner-ilustracao{font-size:clamp(70px,12vw,130px);flex-shrink:0}
.banner-controles{margin-top:16px}
@media(max-width:600px){
    .banner-campanha{padding:24px;min-height:320px;position:relative}
    .banner-ilustracao{position:absolute;right:15px;top:15px;font-size:42px;opacity:.7}
    .banner-campanha .selo{padding-right:48px;font-size:13px}
    .banner-campanha h2{padding-right:16px}
}
@media(prefers-reduced-motion:reduce){.banner-slide{animation:none}}
.banner-slide img{
    display:block;
    width:100%;
    height:clamp(240px,50vw,480px);
    object-fit:contain;
    background:#092719;
    border-radius:12px
}
.banner-slide figcaption{
    font-size:clamp(20px,4vw,30px);
    font-weight:bold;
    text-align:center;
    padding:18px 10px;
    line-height:1.4;
    color:#ffe08a
}
.banner-controles{
    display:flex;
    align-items:center;
    justify-content:center;
    gap:10px;
    flex-wrap:wrap
}
.banner-controles button{
    background:#ffcf45;
    color:#19382b;
    border:0;
    font-weight:bold;
    cursor:pointer
}
</style>

<section class="carrossel-loja"
         aria-label="Banners da Casa das Rações Reis"
         aria-roledescription="carrossel"
         id="carrossel-loja">
<div class="limite">

{% if banners_publicos %}
{% for banner in banners_publicos %}
<figure class="banner-slide"
        {% if not loop.first %}hidden{% endif %}
        aria-label="{{ loop.index }} de {{ banners_publicos|length }}">
<img src="{{ url_for('imagem_banner', banner_id=banner.id) }}"
     alt="{{ banner.slogan }}"
     {% if not loop.first %}loading="lazy"{% endif %}>
<figcaption>{{ banner.slogan }}</figcaption>
</figure>
{% endfor %}
{% else %}
{% for indice, slogan in [
    (1, 'Casa das Rações Reis — produtos para todas as criações'),
    (2, 'Casa das Rações Reis — a parceira do produtor rural em Campo Grande do Piauí')
] %}
<figure class="banner-slide" {% if not loop.first %}hidden{% endif %}
        aria-label="{{ loop.index }} de 2">
<a href="{{ whatsapp }}" target="_blank" rel="noopener noreferrer">
<img src="{{ url_for('banner_fixo', indice=indice) }}" alt="{{ slogan }}"
     {% if not loop.first %}loading="lazy"{% endif %}>
</a>
<figcaption>{{ slogan }}</figcaption>
</figure>
{% endfor %}
{% endif %}

{% if total_banners > 1 %}
<div class="banner-controles">
<button type="button" id="banner-anterior" aria-label="Banner anterior">
← Anterior
</button>
<span id="banner-contador">1 / {{ total_banners }}</span>
<button type="button" id="banner-proximo" aria-label="Próximo banner">
Próximo →
</button>
<button type="button" id="banner-pausa" aria-pressed="false">
Pausar
</button>
</div>
{% endif %}

</div>
</section>

<script>
(function(){
    const area = document.getElementById("carrossel-loja");
    if(!area) return;

    const slides = Array.from(
        area.querySelectorAll(".banner-slide")
    );
    if(slides.length < 2) return;

    const contador = document.getElementById("banner-contador");
    const pausa = document.getElementById("banner-pausa");

    let indice = 0;
    let pausado = window.matchMedia(
        "(prefers-reduced-motion: reduce)"
    ).matches;
    let sobre = false;
    let foco = false;
    let temporizador = null;

    function mostrar(n){
        indice = (n + slides.length) % slides.length;
        slides.forEach((slide, i)=>{
            slide.hidden = i !== indice;
        });
        contador.textContent = (indice + 1) + " / " + slides.length;
    }

    function reiniciar(){
        if(temporizador !== null){
            clearInterval(temporizador);
        }
        temporizador = null;
        pausa.textContent = pausado ? "Reproduzir" : "Pausar";
        pausa.setAttribute("aria-pressed", String(pausado));

        if(!pausado && !sobre && !foco && !document.hidden){
            temporizador = setInterval(
                ()=>mostrar(indice + 1),
                6000
            );
        }
    }

    document.getElementById("banner-anterior").addEventListener(
        "click",
        ()=>{
            mostrar(indice - 1);
            reiniciar();
        }
    );

    document.getElementById("banner-proximo").addEventListener(
        "click",
        ()=>{
            mostrar(indice + 1);
            reiniciar();
        }
    );

    pausa.addEventListener("click", ()=>{
        pausado = !pausado;
        reiniciar();
    });

    area.addEventListener("mouseenter", ()=>{
        sobre = true;
        reiniciar();
    });

    area.addEventListener("mouseleave", ()=>{
        sobre = false;
        reiniciar();
    });

    area.addEventListener("focusin", ()=>{
        foco = true;
        reiniciar();
    });

    area.addEventListener("focusout", (event)=>{
        if(!area.contains(event.relatedTarget)){
            foco = false;
            reiniciar();
        }
    });

    document.addEventListener("visibilitychange", reiniciar);
    reiniciar();
})();
</script>
"""

CATALOGO_HTML = CATALOGO_HTML.replace(
    '<section class="hero">',
    CARROSSEL_HTML + '<section class="hero">',
    1
)


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    porta = int(os.getenv("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=porta
    )

