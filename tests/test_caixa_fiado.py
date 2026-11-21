"""Testes da contagem independente do caixa de fiado."""
import os
import unittest
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "chave-apenas-para-testes"

from app import (
    app, db, Caixa, Cliente, Produto, Venda, VendaGrupo,
    FechamentoFiado, FechamentoCaixa, resumo_caixa_fiado
)


class CaixaFiadoTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        with app.app_context():
            db.drop_all()
            db.create_all()
            cliente = Cliente(nome="Cliente Teste")
            produto = Produto(nome="Milho", custo=40, preco=100, estoque=10)
            db.session.add_all([cliente, produto])
            db.session.flush()

            grupo_fiado = VendaGrupo(
                cliente_id=cliente.id, tipo="fiado", total=100, pago=20
            )
            grupo_vista = VendaGrupo(
                cliente_id=cliente.id, tipo="avista", total=70, pago=70
            )
            db.session.add_all([grupo_fiado, grupo_vista])
            db.session.flush()
            venda = Venda(
                cliente_id=cliente.id, produto_id=produto.id,
                quantidade=1, preco_unitario=100, custo_unitario=40,
                total=100, lucro=60, tipo="fiado", pago=50
            )
            db.session.add(venda)
            db.session.flush()
            self.venda_id = venda.id

            db.session.add_all([
                Caixa(tipo="entrada", descricao=(
                    f"Compra #{grupo_fiado.id} - Cliente Teste - 1 item(ns)"
                ), valor=20),
                Caixa(tipo="entrada", descricao=(
                    f"Pagamento de fiado - Venda #{venda.id}"
                ), valor=30),
                Caixa(tipo="entrada", descricao=(
                    f"Compra #{grupo_vista.id} - Cliente Teste - 1 item(ns)"
                ), valor=70),
                Caixa(tipo="saida", descricao=(
                    f"Estorno por exclusão da venda #{venda.id}"
                ), valor=10),
            ])
            db.session.commit()

        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["logado"] = True
            sess["_csrf_token"] = "token-de-seguranca-para-teste-123456"
            sess["csrf_zerar_fiado"] = "confirmacao-fiado-para-teste-123456"
            sess["csrf_zerar"] = "confirmacao-caixa-para-teste-123456"

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_contagem_reconhece_entrada_parcial_pagamento_e_estorno(self):
        with app.app_context():
            resumo = resumo_caixa_fiado()
            self.assertEqual(resumo["recebido"], Decimal("50.00"))
            self.assertEqual(resumo["estornado"], Decimal("10.00"))
            self.assertEqual(resumo["liquido"], Decimal("40.00"))

    def test_zeramento_fiado_preserva_dividas_caixa_e_historico(self):
        response = self.client.post("/zerar-caixa-fiado", data={
            "_csrf_token": "token-de-seguranca-para-teste-123456",
            "csrf_zerar_fiado": "confirmacao-fiado-para-teste-123456",
            "confirmar": "sim"
        })
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            self.assertEqual(resumo_caixa_fiado()["liquido"], Decimal("0"))
            self.assertEqual(Venda.query.get(self.venda_id).saldo_devedor, Decimal("50"))
            self.assertEqual(Caixa.query.count(), 4)
            self.assertEqual(FechamentoCaixa.query.count(), 0)
            fechamento = FechamentoFiado.query.one()
            self.assertEqual(fechamento.total_recebido, Decimal("50"))
            self.assertEqual(fechamento.total_estornado, Decimal("10"))

            db.session.add(Caixa(
                tipo="entrada",
                descricao=f"Pagamento de fiado - Venda #{self.venda_id}",
                valor=5
            ))
            db.session.commit()
            self.assertEqual(resumo_caixa_fiado()["liquido"], Decimal("5"))

    def test_zeramento_geral_nao_zera_contagem_do_fiado(self):
        response = self.client.post("/zerar-caixa", data={
            "_csrf_token": "token-de-seguranca-para-teste-123456",
            "csrf_zerar": "confirmacao-caixa-para-teste-123456",
            "confirmar": "sim"
        })
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            self.assertEqual(resumo_caixa_fiado()["liquido"], Decimal("40"))
            self.assertEqual(FechamentoFiado.query.count(), 0)

    def test_zeramento_exige_confirmacao(self):
        response = self.client.post("/zerar-caixa-fiado", data={
            "_csrf_token": "token-de-seguranca-para-teste-123456",
            "csrf_zerar_fiado": "confirmacao-fiado-para-teste-123456"
        })
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            self.assertEqual(FechamentoFiado.query.count(), 0)
            self.assertEqual(resumo_caixa_fiado()["liquido"], Decimal("40"))


if __name__ == "__main__":
    unittest.main()
