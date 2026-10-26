import os
import secrets
import hmac
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from flask import (
    Flask, request, redirect, session, flash,
    render_template_string, Response, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
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


def logado():
    return session.get("logado") is True


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
</style>
</head>
<body>
<header>
<h2>🌾 Casa das Rações Reis</h2>
<small>Estoque • Caixa • Vendas • Fiado • Lucro</small>
</header>

{% if session.get("logado") %}
<nav>
<a href="/">Painel</a>
<a href="/produtos">Produtos</a>
<a href="/entrada">Entrada</a>
<a href="/venda">Venda</a>
<a href="/vendas">Vendas / Excluir</a>
<a href="/clientes">Clientes</a>
<a href="/fiado">Fiado</a>
<a href="/caixa">Caixa</a>
<a href="/relatorios">Relatórios</a>
<a href="/analise-negocio">Análise do Negócio</a>
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
</body>
</html>
"""


def pagina(template, **dados):
    conteudo = render_template_string(template, **dados)
    return render_template_string(
        BASE,
        conteudo=Markup(conteudo)
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario_correto = os.getenv("ADMIN_USER", "admin")
        senha_correta = os.getenv("ADMIN_PASSWORD", "admin123")

        usuario = request.form["usuario"]
        senha = request.form["senha"]

        if usuario == usuario_correto and senha == senha_correta:
            session["logado"] = True
            return redirect("/")

        flash("Usuário ou senha incorretos.", "erro")

    return pagina("""
<div class="formulario">
<h1>Entrar</h1>
<form method="post">
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

    baixos = Produto.query.filter(
        Produto.estoque <= Produto.estoque_minimo
    ).all()
    ultimas = vendas_atuais().order_by(Venda.id.desc()).limit(10).all()

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
</div>

<h2>Estoque baixo</h2>
<div class="tabela">
<table>
<tr><th>Produto</th><th>Estoque</th></tr>
{% for produto in baixos %}
<tr>
<td>{{ produto.nome }}</td>
<td>{{ produto.estoque }} {{ produto.unidade }}</td>
</tr>
{% endfor %}
</table>
</div>

<h2>Últimas vendas</h2>
<div class="tabela">
<table>
<tr>
<th>Produto</th><th>Total</th><th>Lucro</th><th>Pagamento</th>
</tr>
{% for venda in ultimas %}
<tr>
<td>{{ venda.produto.nome }}</td>
<td>{{ venda.total|dinheiro }}</td>
<td>{{ venda.lucro|dinheiro }}</td>
<td>{{ "Fiado" if venda.tipo == "fiado" else "À vista" }}</td>
</tr>
{% endfor %}
</table>
</div>
""",
        saldo=saldo,
        total_vendido=total_vendido,
        lucro_total=lucro_total,
        fiado=fiado,
        baixos=baixos,
        ultimas=ultimas
    )


@app.route("/produtos", methods=["GET", "POST"])
def produtos():
    if not logado():
        return redirect("/login")

    if request.method == "POST":
        produto = Produto(
            nome=request.form["nome"],
            categoria=request.form.get("categoria", ""),
            unidade=request.form["unidade"],
            custo=numero(request.form["custo"]),
            preco=numero(request.form["preco"]),
            estoque=numero(request.form["estoque"]),
            estoque_minimo=numero(request.form["estoque_minimo"])
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

        if quantidade <= 0:
            flash("Quantidade inválida.", "erro")
            return redirect("/entrada")

        produto.estoque = Decimal(produto.estoque or 0) + quantidade
        produto.custo = custo

        registro = EntradaEstoque(
            produto_id=produto.id,
            quantidade=quantidade,
            custo_unitario=custo
        )
        db.session.add(registro)

        if request.form.get("registrar_caixa") == "sim":
            valor_total = quantidade * custo
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
<tr>
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
        produto = db.session.get(
            Produto,
            int(request.form["produto"])
        )
        quantidade = numero(request.form["quantidade"])
        tipo = request.form["tipo"]

        if not produto:
            flash("Produto não encontrado.", "erro")
            return redirect("/venda")

        if not quantidade.is_finite() or quantidade <= 0:
            flash("Quantidade inválida.", "erro")
            return redirect("/venda")

        estoque = Decimal(produto.estoque or 0)

        if quantidade > estoque:
            flash("Não há estoque suficiente.", "erro")
            return redirect("/venda")

        preco = Decimal(produto.preco or 0)
        custo = Decimal(produto.custo or 0)
        total = preco * quantidade
        lucro = (preco - custo) * quantidade

        if tipo not in ("avista", "fiado"):
            flash("Escolha à vista ou fiado.", "erro")
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

        if tipo == "avista":
            pago = total
        else:
            try:
                pago = Decimal(
                    request.form.get("entrada", "0").replace(",", ".")
                )
            except Exception:
                flash("Informe um pagamento válido.", "erro")
                return redirect("/venda")

            if not pago.is_finite() or pago < 0 or pago > total:
                flash(
                    "O pagamento deve ficar entre zero e o total da venda.",
                    "erro"
                )
                return redirect("/venda")

        if novo_nome:
            cliente = Cliente(
                nome=novo_nome,
                telefone=novo_telefone
            )
            db.session.add(cliente)
            db.session.flush()
            cliente_id = cliente.id

        nova_venda = Venda(
            produto_id=produto.id,
            cliente_id=cliente_id,
            quantidade=quantidade,
            preco_unitario=preco,
            custo_unitario=custo,
            total=total,
            lucro=lucro,
            tipo=tipo,
            pago=pago
        )
        produto.estoque = estoque - quantidade
        db.session.add(nova_venda)

        if pago > 0:
            db.session.add(Caixa(
                tipo="entrada",
                descricao="Venda - " + produto.nome,
                valor=pago
            ))

        db.session.commit()
        flash("Venda realizada com sucesso.", "sucesso")
        return redirect("/")

    return pagina("""
<h1>Nova venda</h1>
<div class="formulario">
<form method="post">
<label>Produto</label>
<select name="produto">
{% for produto in produtos %}
<option value="{{ produto.id }}">
{{ produto.nome }} |
Estoque: {{ produto.estoque }} |
{{ produto.preco|dinheiro }}
</option>
{% endfor %}
</select>

<label>Quantidade</label>
<input name="quantidade" type="number" step="0.001" required>

<label>Tipo da venda</label>
<select name="tipo">
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

<h2>Cadastrar cliente nesta venda</h2>
<p>
Para um novo cliente, deixe a seleção acima em “Nenhum”.
O cadastro será salvo junto com a venda.
</p>

<label for="novo_cliente_nome">Nome do novo cliente</label>
<input id="novo_cliente_nome" name="novo_cliente_nome" maxlength="150">

<label for="novo_cliente_telefone">Telefone (opcional)</label>
<input id="novo_cliente_telefone" name="novo_cliente_telefone"
       type="tel" maxlength="50">

<label>Valor pago agora em venda fiado</label>
<input name="entrada" type="number" step="0.01" value="0">

<button>Finalizar venda</button>
</form>
</div>
""", produtos=produtos, clientes=clientes)


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

    return pagina("""
<h1>Contas fiado</h1>
<div class="tabela">
<table>
<tr>
<th>Cliente</th>
<th>Produto</th>
<th>Total</th>
<th>Pago</th>
<th>Falta pagar</th>
<th>Receber</th>
</tr>
{% for venda in vendas %}
<tr>
<td>{{ venda.cliente.nome if venda.cliente else "Sem cliente" }}</td>
<td>{{ venda.produto.nome }}</td>
<td>{{ venda.total|dinheiro }}</td>
<td>{{ venda.pago|dinheiro }}</td>
<td>{{ venda.saldo_devedor|dinheiro }}</td>
<td>
{% if venda.saldo_devedor > 0 %}
<form method="post" action="/receber/{{ venda.id }}">
<input name="valor" type="number" step="0.01"
       placeholder="Valor recebido" required>
<button>Receber</button>
</form>
{% else %}
✅ Pago
{% endif %}
</td>
</tr>
{% endfor %}
</table>
</div>
""", vendas=vendas)


@app.route("/receber/<int:venda_id>", methods=["POST"])
def receber(venda_id):
    if not logado():
        return redirect("/login")

    venda = Venda.query.filter_by(id=venda_id).with_for_update().first_or_404()
    if db.session.get(ExclusaoVenda, venda_id):
        flash("Esta venda foi excluída e não pode receber pagamentos.", "erro")
        return redirect("/fiado")
    valor = numero(request.form["valor"])

    if valor <= 0:
        flash("Valor inválido.", "erro")
        return redirect("/fiado")

    saldo = venda.saldo_devedor

    if valor > saldo:
        valor = saldo

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

        if valor <= 0:
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
    movimentos = movimentos_atuais().order_by(
        Caixa.id.desc()
    ).limit(100).all()

    return pagina("""
<h1>Caixa</h1>
<p>Valores desde o último zeramento.
<a href="/zerar-caixa">Zerar caixa e vendas</a></p>
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
<div class="valor">{{ saidas|dinheiro }}</div>
</div>
</div>

<br>
<div class="formulario">
<form method="post">
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
        saidas=saidas
    )


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
<button>Consultar relatório</button>
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
<p>{{ vendas|length }} vendas</p>
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

<h2>Vendas</h2>
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
        fiado_atual=fiado_atual
    )


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

    top_produtos = sorted(
        por_produto.values(),
        key=lambda item: item["total"],
        reverse=True
    )[:8]
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
<p>{{ vendas|length }} vendas registradas</p>
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

