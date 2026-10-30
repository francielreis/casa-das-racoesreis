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
from app import app, db, Produto, Cliente, Venda, VendaGrupo, Caixa


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
        resposta = self.web.post("/login", data={"usuario": "auditor", "senha": "senha-testes"})
        self.assertEqual(resposta.status_code, 302)

    def venda_form(self, tipo="avista", entrada="0"):
        pagina = self.web.get("/venda")
        self.assertEqual(pagina.status_code, 200)
        token = re.search(r'name="chave_requisicao" value="([^"]+)"', pagina.get_data(as_text=True))
        self.assertIsNotNone(token)
        return MultiDict([
            ("chave_requisicao", token.group(1)),
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
            "custo": "90.00", "registrar_caixa": "sim"
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
        resposta = self.web.post("/receber/" + str(vid), data={"valor": "999.00"})
        self.assertEqual(resposta.status_code, 302)
        with app.app_context():
            self.assertEqual(Caixa.query.count(), count)
            self.assertEqual(db.session.get(Venda, vid).pago, Decimal("50.00"))

    def test_rejeitar_movimento_invalido(self):
        resp = self.web.post("/caixa", data={
            "tipo": "entrada", "valor": "NaN", "descricao": "Nao permitido"
        })
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            self.assertEqual(Caixa.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
