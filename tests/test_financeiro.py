"""Testes com banco SQLite isolado: nunca acessam o banco da loja."""
import os
import re
import unittest
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "chave-testes-nao-producao"
os.environ["ADMIN_USER"] = "auditor"
os.environ["ADMIN_PASSWORD"] = "senha-testes"

from werkzeug.datastructures import MultiDict
from app import app, db, Produto, Cliente, Venda, VendaGrupo, Caixa, EntradaEstoque


class AuditoriaFinanceiraTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        with app.app_context():
            db.drop_all()
            db.create_all()
            self.feijao = Produto(
                nome="Feijão 50 kg", categoria="Ração", unidade="saco",
                preco=Decimal("80.00"), custo=Decimal("70.00"),
                estoque=Decimal("10"), estoque_minimo=Decimal("1")
            )
            self.torta = Produto(
                nome="Torta de algodão", categoria="Ração", unidade="saco",
                preco=Decimal("90.00"), custo=Decimal("60.00"),
                estoque=Decimal("10"), estoque_minimo=Decimal("1")
            )
            self.cliente = Cliente(nome="Cliente Teste")
            db.session.add_all([self.feijao, self.torta, self.cliente])
            db.session.commit()
            self.ids = (self.feijao.id, self.torta.id, self.cliente.id)
        self.web = app.test_client()
        resposta = self.web.post("/login", data={
            "usuario": "auditor", "senha": "senha-testes",
            "_csrf_token": self.csrf("/login"),
        })
        self.assertEqual(resposta.status_code, 302)

    def csrf(self, caminho):
        pagina = self.web.get(caminho)
        self.assertEqual(pagina.status_code, 200)
        correspondencia = re.search(
            r'name="_csrf_token" value="([^"]+)"', pagina.get_data(as_text=True)
        )
        self.assertIsNotNone(correspondencia, caminho)
        return correspondencia.group(1)

    def venda_form(self, tipo="avista", entrada="0"):
        pagina = self.web.get("/venda")
        self.assertEqual(pagina.status_code, 200)
        token = re.search(r'name="chave_requisicao" value="([^"]+)"', pagina.get_data(as_text=True))
        self.assertIsNotNone(token)
        return MultiDict([
            ("chave_requisicao", token.group(1)),
            ("_csrf_token", self.csrf("/venda")),
            ("produto", str(self.ids[0])), ("quantidade", "1"),
            ("produto", str(self.ids[1])), ("quantidade", "1"),
            ("tipo", tipo), ("cliente", str(self.ids[2])),
            ("entrada", entrada)
        ])

    def test_compra_dois_itens_e_reenvio_sem_duplicar(self):
        dados = self.venda_form()
        primeira = self.web.post("/venda", data=dados)
        segunda = self.web.post("/venda", data=dados)
        self.assertEqual(primeira.status_code, 302)
        self.assertEqual(segunda.status_code, 302)
        self.assertIn("/vendas/compra/", primeira.location)
        self.assertEqual(primeira.location, segunda.location)
        with app.app_context():
            self.assertEqual(Venda.query.count(), 2)
            self.assertEqual(VendaGrupo.query.count(), 1)
            self.assertEqual(Caixa.query.count(), 1)
            self.assertEqual(VendaGrupo.query.first().total, Decimal("170.00"))
            self.assertEqual(sum((v.lucro for v in Venda.query.all()), Decimal("0")), Decimal("40.00"))
        tela = self.web.get("/auditoria-financeira")
        self.assertEqual(tela.status_code, 200)
        self.assertIn("Nenhuma inconsistência detectada", tela.get_data(as_text=True))
        analise = self.web.get("/analise-negocio")
        self.assertEqual(analise.status_code, 200)
        self.assertIn("1 compra(s)", analise.get_data(as_text=True))

    def test_custo_medio_sem_reescrever_lucro_historico(self):
        self.web.post("/venda", data=self.venda_form())
        resposta = self.web.post("/entrada", data={
            "produto": str(self.ids[0]), "quantidade": "10",
            "custo": "90.00", "registrar_caixa": "sim",
            "_csrf_token": self.csrf("/entrada")
        })
        self.assertEqual(resposta.status_code, 302)
        with app.app_context():
            p = db.session.get(Produto, self.ids[0])
            # Restaram 9 unidades a R$70, entrou 10 a R$90.
            self.assertEqual(p.custo, Decimal("80.53"))
            self.assertEqual(p.estoque, Decimal("19.000"))
            v = Venda.query.filter_by(produto_id=self.ids[0]).first()
            self.assertEqual(v.custo_unitario, Decimal("70.00"))
            self.assertEqual(v.lucro, Decimal("10.00"))

    def test_fiado_nao_aceita_pagamento_maior_que_saldo(self):
        self.web.post("/venda", data=self.venda_form("fiado", "50.00"))
        with app.app_context():
            v = Venda.query.order_by(Venda.id).first()
            vid = v.id
            count = Caixa.query.count()
        resposta = self.web.post("/receber/" + str(vid), data={"valor": "999.00", "_csrf_token": self.csrf("/fiado")})
        self.assertEqual(resposta.status_code, 302)
        with app.app_context():
            self.assertEqual(Caixa.query.count(), count)
            self.assertEqual(db.session.get(Venda, vid).pago, Decimal("50.00"))

    def test_pdf_auditoria_fiado_com_acesso_restrito_e_sem_alterar_dados(self):
        sem_login = app.test_client().get("/auditoria-fiado?formato=pdf")
        self.assertEqual(sem_login.status_code, 302)
        self.assertIn("/login", sem_login.location)

        self.web.post("/venda", data=self.venda_form("fiado", "50.00"))
        with app.app_context():
            vendas_antes = Venda.query.count()
            caixa_antes = Caixa.query.count()
            saldo_antes = sum((v.saldo_devedor for v in Venda.query.all()), Decimal("0"))
            self.assertEqual(saldo_antes, Decimal("120.00"))

        pdf = self.web.get("/auditoria-fiado?formato=pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF-"))
        self.assertGreater(len(pdf.data), 1500)
        self.assertIn("attachment", pdf.headers["Content-Disposition"])
        self.assertIn("no-store", pdf.headers["Cache-Control"])

        with app.app_context():
            self.assertEqual(Venda.query.count(), vendas_antes)
            self.assertEqual(Caixa.query.count(), caixa_antes)
            self.assertEqual(
                sum((v.saldo_devedor for v in Venda.query.all()), Decimal("0")),
                saldo_antes,
            )

    def test_pdf_relatorios_periodos_e_registros_preservados(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        sem_login = app.test_client().get("/relatorios?formato=pdf")
        self.assertEqual(sem_login.status_code, 302)
        self.assertIn("/login", sem_login.location)

        self.web.post("/venda", data=self.venda_form("fiado", "50.00"))
        referencia = datetime.now(ZoneInfo("America/Fortaleza")).strftime("%Y-%m-%d")
        with app.app_context():
            vendas_antes = Venda.query.count()
            caixa_antes = Caixa.query.count()
            valor_antes = sum((v.total for v in Venda.query.all()), Decimal("0"))
            self.assertEqual(valor_antes, Decimal("170.00"))

        for periodo in ("diario", "semanal", "mensal", "personalizado"):
            url = (
                "/relatorios?periodo=personalizado&data_inicio=" + referencia
                + "&data_fim=" + referencia + "&formato=pdf"
                if periodo == "personalizado"
                else "/relatorios?periodo=" + periodo + "&data=" + referencia
                     + "&formato=pdf"
            )
            resposta = self.web.get(url)
            self.assertEqual(resposta.status_code, 200, periodo)
            self.assertEqual(resposta.mimetype, "application/pdf", periodo)
            self.assertTrue(resposta.data.startswith(b"%PDF-"), periodo)
            self.assertGreater(len(resposta.data), 1900, periodo)
            self.assertIn("attachment", resposta.headers["Content-Disposition"])
            self.assertIn("no-store", resposta.headers["Cache-Control"])

        tela = self.web.get("/relatorios?periodo=mensal&data=" + referencia)
        self.assertIn("Baixar PDF do período", tela.get_data(as_text=True))
        invalido = self.web.get(
            "/relatorios?periodo=personalizado&data_inicio=2026-11-02"
            "&data_fim=2026-11-01&formato=pdf"
        )
        self.assertEqual(invalido.status_code, 302)
        with app.app_context():
            self.assertEqual(Venda.query.count(), vendas_antes)
            self.assertEqual(Caixa.query.count(), caixa_antes)
            self.assertEqual(
                sum((v.total for v in Venda.query.all()), Decimal("0")),
                valor_antes,
            )

    def test_pesquisa_global_aparece_nas_telas_e_requer_login(self):
        visitante = app.test_client()
        self.assertIn("/login", visitante.get("/pesquisa?q=feijao").location)
        self.assertNotIn('<form class="pesquisa-global"', visitante.get("/login").get_data(as_text=True))
        for pagina_url in ("/", "/produtos", "/clientes", "/entrada", "/venda",
                           "/vendas", "/fiado", "/caixa", "/relatorios",
                           "/auditoria-financeira", "/auditoria-fiado"):
            resposta = self.web.get(pagina_url)
            self.assertEqual(resposta.status_code, 200, pagina_url)
            html = resposta.get_data(as_text=True)
            self.assertIn('class="pesquisa-global"', html, pagina_url)
            self.assertIn('action="/pesquisa"', html, pagina_url)
            self.assertIn('Filtrar nesta tabela', html, pagina_url)

    def test_busca_encontra_produto_cliente_fiado_venda_caixa_e_entrada(self):
        self.web.post("/venda", data=self.venda_form("fiado", "50.00"))
        with app.app_context():
            ids = [v.id for v in Venda.query.order_by(Venda.id).all()]
            caixa = Caixa.query.first()
            caixa.descricao = "Pagamento de teste"
            entrada = EntradaEstoque(
                produto_id=self.ids[0], quantidade=Decimal("2"),
                custo_unitario=Decimal("70.00")
            )
            db.session.add(entrada)
            db.session.commit()
            entrada_id = entrada.id

        casos = (
            ("/pesquisa?q=Feij%C3%A3o&secao=produtos", "Feijão 50 kg"),
            ("/pesquisa?q=Cliente+Teste&secao=clientes", "Cliente Teste"),
            ("/pesquisa?q=Cliente+Teste&secao=vendas", "Venda #"),
            ("/pesquisa?q=Cliente+Teste&secao=fiados", "Fiado #"),
            ("/pesquisa?q=Pagamento+de+teste&secao=caixa", "Pagamento de teste"),
            ("/pesquisa?q=Feij%C3%A3o&secao=entradas", "Entrada #"),
        )
        for url, esperado in casos:
            resposta = self.web.get(url)
            self.assertEqual(resposta.status_code, 200, url)
            self.assertIn(esperado, resposta.get_data(as_text=True), url)

        numero = self.web.get("/pesquisa?q=%23" + str(ids[0]) + "&secao=vendas")
        self.assertIn("Venda #" + str(ids[0]), numero.get_data(as_text=True))
        detalhe = self.web.get("/vendas/" + str(ids[0]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertIn("A receber", detalhe.get_data(as_text=True))
        self.assertIn("Entrada #" + str(entrada_id),
                      self.web.get("/pesquisa?q=%23" + str(entrada_id)
                                   + "&secao=entradas").get_data(as_text=True))
        self.assertEqual(self.web.get("/pesquisa").status_code, 200)

    def test_pesquisa_nao_mostra_venda_excluida_e_escapa_html(self):
        from app import ExclusaoVenda
        self.web.post("/venda", data=self.venda_form("fiado", "0.00"))
        with app.app_context():
            venda = Venda.query.first()
            db.session.add(ExclusaoVenda(venda_id=venda.id))
            id_excluido = venda.id
            db.session.commit()
        resultado = self.web.get(
            "/pesquisa?q=%23" + str(id_excluido) + "&secao=vendas"
        ).get_data(as_text=True)
        self.assertIn("Nenhum registro encontrado", resultado)
        self.assertEqual(
            self.web.get("/vendas/" + str(id_excluido)).status_code, 404
        )
        injetado = self.web.get("/pesquisa?q=%3Cscript%3Ealert(1)%3C%2Fscript%3E")
        html = injetado.get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_csrf_bloqueia_posts_sem_token_sem_modificar_lancamentos(self):
        rotas = (
            ("/login", {"usuario": "auditor", "senha": "senha-testes"}),
            ("/produtos", {"nome": "Produto falso", "preco": "10", "custo": "5", "unidade": "saco"}),
            ("/clientes", {"nome": "Cliente falso"}),
            ("/entrada", {"produto": str(self.ids[0]), "quantidade": "2", "custo": "70"}),
            ("/venda", self.venda_form()),
            ("/caixa", {"tipo": "entrada", "valor": "150", "descricao": "Entrada falsa"}),
            ("/despesas", {"categoria": "Aluguel", "valor": "10"}),
            ("/banners", {"acao": "salvar"}),
            ("/zerar-caixa", {"confirmar": "sim"}),
            ("/produtos/" + str(self.ids[0]) + "/preco", {"preco": "999", "custo": "1"}),
        )
        for caminho, dados in rotas:
            resposta = self.web.post(caminho, data={
                k: v for k, v in dados.items() if k != "_csrf_token"
            })
            self.assertEqual(resposta.status_code, 400, caminho)

        with app.app_context():
            self.assertEqual(Venda.query.count(), 0)
            self.assertEqual(VendaGrupo.query.count(), 0)
            self.assertEqual(Caixa.query.count(), 0)
            self.assertEqual(db.session.get(Produto, self.ids[0]).preco, Decimal("80.00"))

        outro_navegador = app.test_client()
        outro_navegador.get("/login")
        resposta = outro_navegador.post("/login", data={
            "usuario": "auditor", "senha": "senha-testes",
            "_csrf_token": self.csrf("/login"),
        })
        self.assertEqual(resposta.status_code, 400)

    def test_csrf_recebimento_valido_e_falso_bloqueado(self):
        self.web.post("/venda", data=self.venda_form("fiado", "50.00"))
        with app.app_context():
            venda_id = Venda.query.order_by(Venda.id).first().id
            caixa_antes = Caixa.query.count()
        rota = "/receber/" + str(venda_id)
        self.assertEqual(self.web.post(rota, data={"valor": "25.00"}).status_code, 400)
        valido = self.web.post(rota, data={
            "valor": "25.00", "_csrf_token": self.csrf("/fiado")
        })
        self.assertEqual(valido.status_code, 302)
        with app.app_context():
            self.assertEqual(db.session.get(Venda, venda_id).pago, Decimal("75.00"))
            self.assertEqual(Caixa.query.count(), caixa_antes + 1)

    def test_formularios_post_contem_token_csrf(self):
        # O formulario "Receber" so aparece quando existe um fiado registrado.
        self.web.post("/venda", data=self.venda_form("fiado", "0.00"))
        paginas = ["/login", "/produtos", "/clientes", "/entrada", "/venda",
                   "/fiado", "/caixa", "/despesas", "/banners", "/zerar-caixa",
                   "/produtos/" + str(self.ids[0]) + "/preco"]
        for pagina in paginas:
            html = self.web.get(pagina).get_data(as_text=True)
            formularios = re.findall(
                r'<form\b[^>]*\bmethod=["\']post["\'][^>]*>.*?</form>',
                html, flags=re.I | re.S,
            )
            self.assertTrue(formularios, pagina)
            for formulario in formularios:
                self.assertIn('name="_csrf_token"', formulario, pagina)

    def test_rejeitar_movimento_invalido(self):
        resp = self.web.post("/caixa", data={
            "tipo": "entrada", "valor": "NaN", "descricao": "Nao permitido",
            "_csrf_token": self.csrf("/caixa")
        })
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            self.assertEqual(Caixa.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
