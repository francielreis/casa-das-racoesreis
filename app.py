import os
from datetime import datetime
from decimal import Decimal

from flask import Flask, request, redirect, session, flash, render_template_string
from flask_sqlalchemy import SQLAlchemy


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "troque-esta-chave-depois")

database_url = os.getenv("DATABASE_URL", "sqlite:///casa_racoes.db")

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# =====================================================
# TABELAS DO BANCO
# =====================================================

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

        return float((self.lucro_unitario / custo) * 100)


class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    telefone = db.Column(db.String(50), default="")


class EntradaEstoque(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(db.Integer, db.ForeignKey("produto.id"), nullable=False)
    quantidade = db.Column(db.Numeric(12, 3), nullable=False)
    custo_unitario = db.Column(db.Numeric(12, 2), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now)

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

    data = db.Column(db.DateTime, default=datetime.now)

    produto = db.relationship("Produto")
    cliente = db.relationship("Cliente")

    @property
    def saldo_devedor(self):
        saldo = Decimal(self.total or 0) - Decimal(self.pago or 0)
        return max(saldo, Decimal("0"))


class Caixa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    data = db.Column(db.DateTime, default=datetime.now)


# =====================================================
# FUNÇÕES
# =====================================================

def numero(valor):
    try:
        return Decimal(str(valor).replace(",", "."))
    except:
        return Decimal("0")


def dinheiro(valor):
    valor = Decimal(valor or 0)

    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")

    return f"R$ {texto}"


app.jinja_env.filters["dinheiro"] = dinheiro


def esta_logado():
    return session.get("logado") is True


# =====================================================
# VISUAL
# =====================================================

BASE = """
<!DOCTYPE html>
<html lang="pt-br">

<head>

<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Casa das Rações</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f3f5f4;
    font-family: Arial, sans-serif;
    color: #222;
}

header {
    background: #176b3a;
    color: white;
    padding: 18px;
}

header h2 {
    margin: 0;
}

nav {
    background: white;
    padding: 8px;
    overflow-x: auto;
    white-space: nowrap;
    border-bottom: 1px solid #ddd;
}

nav a {
    display: inline-block;
    text-decoration: none;
    color: #176b3a;
    font-weight: bold;
    padding: 11px;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 15px;
}

.cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
}

.card {
    background: white;
    padding: 18px;
    border-radius: 10px;
    box-shadow: 0 2px 7px #00000015;
}

.card h3 {
    font-size: 14px;
    color: #666;
    margin-top: 0;
}

.valor {
    color: #176b3a;
    font-weight: bold;
    font-size: 23px;
}

.formulario {
    max-width: 650px;
    background: white;
    padding: 18px;
    border-radius: 10px;
}

input,
select {
    width: 100%;
    padding: 11px;
    margin: 5px 0 13px 0;
    border: 1px solid #bbb;
    border-radius: 6px;
}

button {
    background: #176b3a;
    color: white;
    padding: 12px 18px;
    border: none;
    border-radius: 7px;
    font-weight: bold;
}

table {
    width: 100%;
    background: white;
    border-collapse: collapse;
}

th,
td {
    padding: 10px;
    border-bottom: 1px solid #ddd;
    text-align: left;
}

th {
    background: #e9eceb;
}

.sucesso {
    background: #d1e7dd;
    padding: 12px;
    border-radius: 6px;
    margin-bottom: 12px;
}

.erro {
    background: #f8d7da;
    padding: 12px;
    border-radius: 6px;
    margin-bottom: 12px;
}

h1 {
    color: #176b3a;
}

.tabela {
    overflow-x: auto;
}

</style>

</head>

<body>

<header>
<h2>🌾 Casa das Rações</h2>
<small>Estoque • Caixa • Vendas • Fiado • Lucro</small>
</header>

{% if session.get("logado") %}

<nav>

<a href="/">Painel</a>
<a href="/produtos">Produtos</a>
<a href="/entrada">Entrada</a>
<a href="/venda">Venda</a>
<a href="/clientes">Clientes</a>
<a href="/fiado">Fiado</a>
<a href="/caixa">Caixa</a>
<a href="/logout">Sair</a>

</nav>

{% endif %}

<div class="container">

{% with mensagens = get_flashed_messages(with_categories=true) %}

{% for categoria, mensagem in mensagens %}

<div class="{{ categoria }}">
{{ mensagem }}
</div>

{% endfor %}

{% endwith %}

{{ conteudo|safe }}

</div>

</body>
</html>
"""


def pagina(template, **dados):

    conteudo = render_template_string(template, **dados)

    return render_template_string(
        BASE,
        conteudo=conteudo
    )


# =====================================================
# LOGIN
# =====================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        usuario_correto = os.getenv("ADMIN_USER", "admin")
        senha_correta = os.getenv("ADMIN_PASSWORD", "admin123")

        if (
            request.form["usuario"] == usuario_correto
            and request.form["senha"] == senha_correta
        ):
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

    <p>
    Usuário inicial: <b>admin</b><br>
    Senha inicial: <b>admin123</b>
    </p>

    </div>
    """)


@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# =====================================================
# PAINEL
# =====================================================

@app.route("/")
def painel():

    if not esta_logado():
        return redirect("/login")

    vendas = Venda.query.all()

    total_vendido = sum(
        (Decimal(v.total or 0) for v in vendas),
        Decimal("0")
    )

    lucro = sum(
        (Decimal(v.lucro or 0) for v in vendas),
        Decimal("0")
    )

    fiado = sum(
        (
            v.saldo_devedor
            for v in vendas
            if v.tipo == "fiado"
        ),
        Decimal("0")
    )

    movimentos = Caixa.query.all()

    entradas = sum(
        (
            Decimal(m.valor)
            for m in movimentos
            if m.tipo == "entrada"
        ),
        Decimal("0")
    )

    saidas = sum(
        (
            Decimal(m.valor)
            for m in movimentos
            if m.tipo == "saida"
        ),
        Decimal("0")
    )

    saldo = entradas - saidas

    baixos = Produto.query.filter(
        Produto.estoque <= Produto.estoque_minimo
    ).all()

    ultimas = Venda.query.order_by(
        Venda.id.desc()
    ).limit(10).all()

    return pagina("""

    <h1>Painel</h1>

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
    <div class="valor">{{ lucro|dinheiro }}</div>
    </div>

    <div class="card">
    <h3>📝 Fiado a receber</h3>
    <div class="valor">{{ fiado|dinheiro }}</div>
    </div>

    </div>

    <h2>Estoque baixo</h2>

    <div class="tabela">

    <table>

    <tr>
    <th>Produto</th>
    <th>Estoque</th>
    </tr>

    {% for p in baixos %}

    <tr>
    <td>{{ p.nome }}</td>
    <td>{{ p.estoque }} {{ p.unidade }}</td>
    </tr>

    {% endfor %}

    </table>

    </div>

    <h2>Últimas vendas</h2>

    <div class="tabela">

    <table>

    <tr>
    <th>Produto</th>
    <th>Total</th>
    <th>Lucro</th>
    <th>Pagamento</th>
    </tr>

    {% for v in ultimas %}

    <tr>

    <td>{{ v.produto.nome }}</td>
    <td>{{ v.total|dinheiro }}</td>
    <td>{{ v.lucro|dinheiro }}</td>
    <td>{{ "Fiado" if v.tipo == "fiado" else "À vista" }}</td>

    </tr>

    {% endfor %}

    </table>

    </div>

    """,
    saldo=saldo,
    total_vendido=total_vendido,
    lucro=lucro,
    fiado=fiado,
    baixos=baixos,
    ultimas=ultimas
    )


# =====================================================
# PRODUTOS
# =====================================================

@app.route("/produtos", methods=["GET", "POST"])
def produtos():

    if not esta_logado():
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
    <th>Lucro/un.</th>
    <th>Margem</th>
    <th>Estoque</th>
    </tr>

    {% for p in produtos %}

    <tr>

    <td>{{ p.nome }}</td>
    <td>{{ p.custo|dinheiro }}</td>
    <td>{{ p.preco|dinheiro }}</td>
    <td>{{ p.lucro_unitario|dinheiro }}</td>
    <td>{{ "%.2f"|format(p.margem) }}%</td>
    <td>{{ p.estoque }} {{ p.unidade }}</td>

    </tr>

    {% endfor %}

    </table>

    </div>

    """,
    produtos=lista
    )


# =====================================================
# ENTRADA DE MERCADORIA
# =====================================================

@app.route("/entrada", methods=["GET", "POST"])
def entrada():

    if not esta_logado():
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

        registrar_caixa = request.form.get("registrar_caixa")

        if registrar_caixa == "sim":

            valor_total = quantidade * custo

            db.session.add(
                Caixa(
                    tipo="saida",
                    descricao="Compra de mercadoria - " + produto.nome,
                    valor=valor_total
                )
            )

        db.session.commit()

        flash("Entrada de mercadoria registrada.", "sucesso")

        return redirect("/entrada")

    return pagina("""

    <h1>Entrada de mercadoria</h1>

    <div class="formulario">

    <form method="post">

    <label>Produto</label>

    <select name="produto">

    {% for p in produtos %}

    <option value="{{ p.id }}">
    {{ p.nome }}
    </option>

    {% endfor %}

    </select>

    <label>Quantidade</label>
    <input name="quantidade" type="number" step="0.001" required>

    <label>Custo por unidade/saco</label>
    <input name="custo" type="number" step="0.01" required>

    <label>
    <input
    style="width:auto"
    type="checkbox"
    name="registrar_caixa"
    value="sim">
    Registrar esta compra como saída do caixa
    </label>

    <br><br>

    <button>Registrar entrada</button>

    </form>

    </div>

    """,
    produtos=produtos
    )


# =====================================================
# CLIENTES
# =====================================================

@app.route("/clientes", methods=["GET", "POST"])
def clientes():

    if not esta_logado():
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

    <tr>
    <th>Nome</th>
    <th>Telefone</th>
    </tr>

    {% for c in clientes %}

    <tr>
    <td>{{ c.nome }}</td>
    <td>{{ c.telefone }}</td>
    </tr>

    {% endfor %}

    </table>

    </div>

    """,
    clientes=lista
    )


# =====================================================
# VENDA
# =====================================================

@app.route("/venda", methods=["GET", "POST"])
def venda():

    if not esta_logado():
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

        if quantidade <= 0:
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

        cliente_id = request.form.get("cliente")

        if not cliente_id:
            cliente_id = None

        if tipo == "fiado" and not cliente_id:
            flash("Escolha um cliente para venda fiado.", "erro")
            return redirect("/venda")

        if tipo == "avista":
            pago = total
        else:
            pago = numero(request.form.get("entrada", "0"))

            if pago > total:
                pago = total

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

            db.session.add(
                Caixa(
                    tipo="entrada",
                    descricao="Venda - " + produto.nome,
                    valor=pago
                )
            )

        db.session.commit()

        flash("Venda realizada com sucesso.", "sucesso")

        return redirect("/")

    return pagina("""

    <h1>Nova venda</h1>

    <div class="formulario">

    <form method="post">

    <label>Produto</label>

    <select name="produto">

    {% for p in produtos %}

    <option value="{{ p.id }}">
    {{ p.nome }} |
    estoque {{ p.estoque }} |
    {{ p.preco|dinheiro }}
    </option>

    {% endfor %}

    </select>

    <label>Quantidade</label>
    <input name="quantidade" type="number" step="0.001" required>

    <label>Tipo da venda</label>

    <select name="tipo">

    <option value="avista">
    À vista
    </option>

    <option value="fiado">
    Fiado
    </option>

    </select>

    <label>Cliente</label>

    <select name="cliente">

    <option value="">
    Nenhum
    </option>

    {% for c in clientes %}

    <option value="{{ c.id }}">
    {{ c.nome }}
    </option>

    {% endfor %}

    </select>

    <label>
    Valor recebido agora em uma venda fiado
    </label>

    <input
    name="entrada"
    type="number"
    step="0.01"
    value="0">

    <button>Finalizar venda</button>

    </form>

    </div>

    """,
    produtos=produtos,
    clientes=clientes
    )


# =====================================================
# FIADO
# =====================================================

@app.route("/fiado")
def fiado():

    if not esta_logado():
        return redirect("/login")

    vendas = Venda.query.filter(
        Venda.tipo == "fiado"
    ).order_by(
        Venda.id.desc()
    ).all()

    return pagina("""

    <h1>Contas fiado</h1>

    <div class="tabela">

    <table>

    <tr>
    <th>Cliente</th>
    <th>Produto</th>
    <th>Total</th>
    <th>Pago</th>
    <th>Falta
