import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal

from flask import (
    Flask, request, redirect, session,
    flash, render_template_string
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
        return (
            Decimal(self.preco or 0)
            - Decimal(self.custo or 0)
        )

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

    quantidade = db.Column(
        db.Numeric(12, 3),
        nullable=False
    )

    custo_unitario = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

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

    quantidade = db.Column(
        db.Numeric(12, 3),
        nullable=False
    )

    preco_unitario = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    custo_unitario = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    total = db.Column(db.Numeric(12, 2), nullable=False)
    lucro = db.Column(db.Numeric(12, 2), nullable=False)
    tipo = db.Column(db.String(20), default="avista")
    pago = db.Column(db.Numeric(12, 2), default=0)
    data = db.Column(db.DateTime, default=agora_utc)

    produto = db.relationship("Produto")
    cliente = db.relationship("Cliente")

    @property
    def saldo_devedor(self):
        saldo = (
            Decimal(self.total or 0)
            - Decimal(self.pago or 0)
        )

        if saldo < 0:
            return Decimal("0")

        return saldo


class Caixa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    data = db.Column(db.DateTime, default=agora_utc)


def numero(valor):
    try:
        return Decimal(str(valor).replace(",", "."))
    except:
        return Decimal("0")


def dinheiro(valor):
    valor = Decimal(valor or 0)
    texto = f"{valor:,.2f}"
    texto = (
        texto.replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )
    return f"R$ {texto}"


app.jinja_env.filters["dinheiro"] = dinheiro


def logado():
    return session.get("logado") is True


BASE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">
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
    grid-template-columns:
        repeat(auto-fit, minmax(160px, 1fr));
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
input, select {
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
th, td {
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
.aviso {
    background: #fff3cd;
    padding: 12px;
    border-radius: 6px;
}
</style>
</head>

<body>
<header>
    <h2>🌾 Casa das Rações</h2>
    <small>
        Estoque • Caixa • Vendas • Fiado • Lucro
    </small>
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
    <a href="/relatorios">Relatórios</a>
    <a href="/logout">Sair</a>
</nav>
{% endif %}

<div class="container">
    {% with mensagens =
        get_flashed_messages(with_categories=true) %}
        {% for categoria, mensagem in mensagens %}
            <div class="{{ categoria }}">
                {{ mensagem }}
            </div>
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
        senha_correta = os.getenv(
            "ADMIN_PASSWORD", "admin123"
        )

        usuario = request.form["usuario"]
        senha = request.form["senha"]

        if (
            usuario == usuario_correto
            and senha == senha_correta
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

        <br>

        <div class="aviso">
            Usuário inicial: <b>admin</b>
            <br>
            Senha inicial: <b>admin123</b>
        </div>
    </div>
    """)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/")
def painel():
    if not logado():
        return redirect("/login")

    vendas = Venda.query.all()

    total_vendido = sum(
        (Decimal(v.total or 0) for v in vendas),
        Decimal("0")
    )

    lucro_total = sum(
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
            <div class="valor">
                {{ total_vendido|dinheiro }}
            </div>
        </div>

        <div class="card">
            <h3>📈 Lucro bruto</h3>
            <div class="valor">
                {{ lucro_total|dinheiro }}
            </div>
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

            {% for produto in baixos %}
            <tr>
                <td>{{ produto.nome }}</td>
                <td>
                    {{ produto.estoque }}
                    {{ produto.unidade }}
                </td>
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

            {% for venda in ultimas %}
            <tr>
                <td>{{ venda.produto.nome }}</td>
                <td>{{ venda.total|dinheiro }}</td>
                <td>{{ venda.lucro|dinheiro }}</td>
                <td>
                    {% if venda.tipo == "fiado" %}
                        Fiado
                    {% else %}
                        À vista
                    {% endif %}
                </td>
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
            estoque_minimo=numero(
                request.form["estoque_minimo"]
            )
        )

        db.session.add(produto)
        db.session.commit()

        flash("Produto cadastrado com sucesso.", "sucesso")
        return redirect("/produtos")

    lista = Produto.query.order_by(Produto.nome).all()
    vendas = Venda.query.all()

    lucros_por_produto = {}

    for venda in vendas:
        produto_id = venda.produto_id

        if produto_id not in lucros_por_produto:
            lucros_por_produto[produto_id] = Decimal("0")

        lucros_por_produto[produto_id] += Decimal(
            venda.lucro or 0
        )

    return pagina("""
    <h1>Produtos</h1>

    <div class="formulario">
        <form method="post">
            <label>Nome do produto</label>
            <input name="nome"
                   placeholder="Ex: Milho 60 kg" required>

            <label>Categoria</label>
            <input name="categoria"
                   placeholder="Ex: Ração, milho, suplemento">

            <label>Unidade</label>
            <select name="unidade">
                <option value="saco">Saco</option>
                <option value="kg">Kg</option>
                <option value="un">Unidade</option>
            </select>

            <label>Preço de compra</label>
            <input name="custo" type="number"
                   step="0.01" required>

            <label>Preço de venda</label>
            <input name="preco" type="number"
                   step="0.01" required>

            <label>Estoque inicial</label>
            <input name="estoque" type="number"
                   step="0.001" value="0">

            <label>Estoque mínimo</label>
            <input name="estoque_minimo" type="number"
                   step="0.001" value="2">

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
                <th>Lucro acumulado</th>
                <th>Estoque</th>
            </tr>

            {% for produto in produtos %}
            <tr>
                <td>{{ produto.nome }}</td>
                <td>{{ produto.custo|dinheiro }}</td>
                <td>{{ produto.preco|dinheiro }}</td>
                <td>
                    {{ produto.lucro_unitario|dinheiro }}
                </td>
                <td>
                    {{ "%.2f"|format(produto.margem) }}%
                </td>
                <td>
                    {{ lucros.get(produto.id, 0)|dinheiro }}
                </td>
                <td>
                    {{ produto.estoque }}
                    {{ produto.unidade }}
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    """,
        produtos=lista,
        lucros=lucros_por_produto
    )


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

        produto.estoque = (
            Decimal(produto.estoque or 0) + quantidade
        )

        produto.custo = custo

        registro = EntradaEstoque(
            produto_id=produto.id,
            quantidade=quantidade,
            custo_unitario=custo
        )

        db.session.add(registro)

        registrar_caixa = request.form.get(
            "registrar_caixa"
        )

        if registrar_caixa == "sim":
            valor_total = quantidade * custo

            db.session.add(Caixa(
                tipo="saida",
                descricao=(
                    "Compra de mercadoria - " + produto.nome
                ),
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
                <option value="{{ produto.id }}">
                    {{ produto.nome }}
                </option>
                {% endfor %}
            </select>

            <label>Quantidade</label>
            <input name="quantidade" type="number"
                   step="0.001" required>

            <label>Custo por unidade ou saco</label>
            <input name="custo" type="number"
                   step="0.01" required>

            <label>
                <input style="width:auto"
                       type="checkbox"
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
            <tr>
                <th>Nome</th>
                <th>Telefone</th>
            </tr>

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

        cliente_id = request.form.get(
            "cliente", ""
        ).strip()

        novo_nome = request.form.get(
            "novo_cliente_nome", ""
        ).strip()

        novo_telefone = request.form.get(
            "novo_cliente_telefone", ""
        ).strip()

        if cliente_id and (novo_nome or novo_telefone):
            flash(
                "Selecione um cliente cadastrado "
                "ou informe um novo cliente.",
                "erro"
            )
            return redirect("/venda")

        if len(novo_nome) > 150 or len(novo_telefone) > 50:
            flash(
                "Nome ou telefone do cliente muito longo.",
                "erro"
            )
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
                "Selecione um cliente ou cadastre "
                "um novo para vender fiado.",
                "erro"
            )
            return redirect("/venda")

        if tipo == "avista":
            pago = total
        else:
            try:
                pago = Decimal(
                    request.form.get(
                        "entrada", "0"
                    ).replace(",", ".")
                )
            except Exception:
                flash("Informe um pagamento válido.", "erro")
                return redirect("/venda")

            if (
                not pago.is_finite()
                or pago < 0
                or pago > total
            ):
                flash(
                    "O pagamento deve ficar entre zero "
                    "e o total da venda.",
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
                    {{ produto.nome }}
                    | Estoque: {{ produto.estoque }}
                    | {{ produto.preco|dinheiro }}
                </option>
                {% endfor %}
            </select>

            <label>Quantidade</label>
            <input name="quantidade" type="number"
                   step="0.001" required>

            <label>Tipo da venda</label>
            <select name="tipo">
                <option value="avista">À vista</option>
                <option value="fiado">Fiado</option>
            </select>

            <label for="cliente">Cliente cadastrado</label>
            <select name="cliente" id="cliente">
                <option value="">Nenhum</option>

                {% for cliente in clientes %}
                <option value="{{ cliente.id }}">
                    {{ cliente.nome }}
                </option>
                {% endfor %}
            </select>

            <h2>Cadastrar cliente nesta venda</h2>

            <p>
                Para um novo cliente, deixe a seleção
                acima em “Nenhum”.
                O cadastro será salvo junto com a venda.
            </p>

            <label for="novo_cliente_nome">
                Nome do novo cliente
            </label>
            <input id="novo_cliente_nome"
                   name="novo_cliente_nome"
                   maxlength="150">

            <label for="novo_cliente_telefone">
                Telefone (opcional)
            </label>
            <input id="novo_cliente_telefone"
                   name="novo_cliente_telefone"
                   type="tel" maxlength="50">

            <label>
                Valor pago agora em venda fiado
            </label>
            <input name="entrada" type="number"
                   step="0.01" value="0">

            <button>Finalizar venda</button>
        </form>
    </div>
    """, produtos=produtos, clientes=clientes)


@app.route("/fiado")
def fiado():
    if not logado():
        return redirect("/login")

    vendas = Venda.query.filter(
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
                <td>
                    {% if venda.cliente %}
                        {{ venda.cliente.nome }}
                    {% else %}
                        Sem cliente
                    {% endif %}
                </td>

                <td>{{ venda.produto.nome }}</td>
                <td>{{ venda.total|dinheiro }}</td>
                <td>{{ venda.pago|dinheiro }}</td>
                <td>{{ venda.saldo_devedor|dinheiro }}</td>

                <td>
                    {% if venda.saldo_devedor > 0 %}
                    <form method="post"
                          action="/receber/{{ venda.id }}">
                        <input name="valor" type="number"
                               step="0.01"
                               placeholder="Valor recebido"
                               required>
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

    venda = db.get_or_404(Venda, venda_id)
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
        descricao=(
            "Pagamento de fiado - Venda #" + str(venda.id)
        ),
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

    todos_movimentos = Caixa.query.all()

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

    movimentos = Caixa.query.order_by(
        Caixa.id.desc()
    ).limit(100).all()

    return pagina("""
    <h1>Caixa</h1>

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
            <input name="valor" type="number"
                   step="0.01" required>

            <button>Registrar</button>
        </form>
    </div>

    <h2>Movimentações</h2>

    <div class="tabela">
        <table>
            <tr>
                <th>Data</th>
                <th>Descrição</th>
                <th>Tipo</th>
                <th>Valor</th>
            </tr>

            {% for movimento in movimentos %}
            <tr>
                <td>
                    {{ movimento.data.strftime("%d/%m/%Y %H:%M") }}
                </td>
                <td>{{ movimento.descricao }}</td>
                <td>
                    {% if movimento.tipo == "entrada" %}
                        Entrada
                    {% else %}
                        Saída
                    {% endif %}
                </td>
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


@app.route("/relatorios")
def relatorios():
    if not logado():
        return redirect("/login")

    periodo = request.args.get("periodo", "diario")

    if periodo not in ("diario", "semanal", "mensal"):
        flash("Período inválido.", "erro")
        return redirect("/relatorios")

    try:
        referencia = datetime.strptime(
            request.args.get(
                "data",
                datetime.now(FUSO_LOJA).strftime("%Y-%m-%d")
            ),
            "%Y-%m-%d"
        ).replace(tzinfo=FUSO_LOJA)
    except ValueError:
        flash("Data inválida.", "erro")
        return redirect("/relatorios")

    if periodo == "semanal":
        inicio_local = referencia - timedelta(
            days=referencia.weekday()
        )
        fim_local = inicio_local + timedelta(days=7)

    elif periodo == "mensal":
        inicio_local = referencia.replace(day=1)

        if inicio_local.month == 12:
            fim_local = inicio_local.replace(
                year=inicio_local.year + 1,
                month=1
            )
        else:
            fim_local = inicio_local.replace(
                month=inicio_local.month + 1
            )

    else:
        inicio_local = referencia
        fim_local = inicio_local + timedelta(days=1)

    inicio = inicio_local.astimezone(
        timezone.utc
    ).replace(tzinfo=None)

    fim = fim_local.astimezone(
        timezone.utc
    ).replace(tzinfo=None)

    vendas = Venda.query.filter(
        Venda.data >= inicio,
        Venda.data < fim
    ).order_by(
        Venda.data.desc(),
        Venda.id.desc()
    ).all()

    movimentos = Caixa.query.filter(
        Caixa.data >= inicio,
        Caixa.data < fim
    ).order_by(
        Caixa.data.desc(),
        Caixa.id.desc()
    ).all()

    total = sum(
        (v.total for v in vendas),
        Decimal("0")
    )

    lucro = sum(
        (v.lucro for v in vendas),
        Decimal("0")
    )

    entradas = sum(
        (
            m.valor
            for m in movimentos
            if m.tipo == "entrada"
        ),
        Decimal("0")
    )

    saidas = sum(
        (
            m.valor
            for m in movimentos
            if m.tipo == "saida"
        ),
        Decimal("0")
    )

    fiado_atual = sum(
        (
            v.saldo_devedor
            for v in vendas
            if v.tipo == "fiado"
        ),
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
                ("mensal", "Mensal")
            ] %}
            <option value="{{ valor }}"
                    {% if periodo == valor %}selected{% endif %}>
                {{ titulo }}
            </option>
            {% endfor %}
        </select>

        <label for="data">Data de referência</label>
        <input id="data" name="data" type="date"
               value="{{ referencia }}" required>

        <button>Consultar relatório</button>
    </form>

    <p>
        {{ inicio_local.strftime("%d/%m/%Y") }}
        a {{ ultimo_dia.strftime("%d/%m/%Y") }}.
        Horário do Piauí.
        Semana de segunda a domingo.
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
            <div class="valor">
                {{ (entradas - saidas)|dinheiro }}
            </div>
        </div>

        <div class="card">
            <h3>
                Fiado ainda pendente das vendas deste período
            </h3>
            <div class="valor">{{ fiado_atual|dinheiro }}</div>
        </div>
    </div>

    <p>
        O fiado mostra a dívida atual,
        incluindo pagamentos posteriores.
        Entradas de caixa incluem recebimentos de
        vendas antigas e lançamentos manuais.
        Lucro bruto não desconta despesas.
        Movimentação líquida não é o saldo
        acumulado do caixa.
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
                <td>
                    {{ (v.data|data_local).strftime("%d/%m/%Y %H:%M") }}
                </td>
                <td>
                    {{ v.cliente.nome if v.cliente else "Sem cliente" }}
                </td>
                <td>{{ v.produto.nome }}</td>
                <td>
                    {{ v.quantidade }} {{ v.produto.unidade }}
                </td>
                <td>{{ v.total|dinheiro }}</td>
                <td>{{ v.lucro|dinheiro }}</td>
                <td>
                    {{ "Fiado" if v.tipo == "fiado" else "À vista" }}
                </td>
            </tr>
            {% else %}
            <tr>
                <td colspan="7">
                    Nenhuma venda neste período.
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>

    <h2>Movimentações de caixa</h2>

    <div class="tabela">
        <table>
            <tr>
                <th>Data</th>
                <th>Descrição</th>
                <th>Tipo</th>
                <th>Valor</th>
            </tr>

            {% for m in movimentos %}
            <tr>
                <td>
                    {{ (m.data|data_local).strftime("%d/%m/%Y %H:%M") }}
                </td>
                <td>{{ m.descricao }}</td>
                <td>
                    {{ "Entrada" if m.tipo == "entrada" else "Saída" }}
                </td>
                <td>{{ m.valor|dinheiro }}</td>
            </tr>
            {% else %}
            <tr>
                <td colspan="4">
                    Nenhuma movimentação neste período.
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    """,
        periodo=periodo,
        referencia=referencia.strftime("%Y-%m-%d"),
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


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    porta = int(os.getenv("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=porta
    )
