"""Testes de recebimento integral de fiado com banco de teste isolado."""
import os
import unittest
from decimal import Decimal

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "segredo-apenas-para-testes"

from app import app, db, Caixa, Cliente, Produto, Venda, ExclusaoVenda


class ReceberTotalFiadoTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        with app.app_context():
            db.drop_all()
            db.create_all()
            cliente = Cliente(nome="Biluca")
            outro = Cliente(nome="Outro cliente")
            produto = Produto(nome="Torta de algodão", custo=60, preco=93, estoque=50)
            db.session.add_all([cliente, outro, produto])
            db.session.flush()
            self.cliente_id = cliente.id
            self.outro_id = outro.id
            for cliente_id, total, pago, tipo in [
                (cliente.id, "93.00", "0.00", "fiado"),
                (cliente.id, "170.00", "50.00", "fiado"),
                (outro.id, "80.00", "0.00", "fiado"),
                (cliente.id, "40.00", "40.00", "avista"),
            ]:
                db.session.add(Venda(
                    cliente_id=cliente_id, produto_id=produto.id,
                    quantidade=1, preco_unitario=Decimal(total),
                    custo_unitario=Decimal("60.00"),
                    total=Decimal(total), lucro=Decimal(total)-Decimal("60.00"),
                    tipo=tipo, pago=Decimal(pago)
                ))
            db.session.commit()
            self.ids = [v.id for v in Venda.query.order_by(Venda.id).all()]

        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["logado"] = True
            sess["_csrf_token"] = "csrf-seguro-para-testes-1234567890"

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def post(self, path, data):
        return self.client.post(
            path, data={"_csrf_token": "csrf-seguro-para-testes-1234567890", **data}
        )

    def test_tela_oferece_ambas_opcoes_e_valor_total_cliente(self):
        res = self.client.get("/fiado")
        html = res.get_data(as_text=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Receber parcial", html)
        self.assertIn("Receber total</button>", html)
        self.assertIn("Receber total do cliente — R$ 213,00", html)
        self.assertIn("formnovalidate", html)

    def test_recebe_total_compra_sem_campo_valor(self):
        res = self.post(f"/receber/{self.ids[0]}", {
            "modo": "total", "saldo_conferido": "93.00"
        })
        self.assertEqual(res.status_code, 302)
        with app.app_context():
            self.assertEqual(db.session.get(Venda, self.ids[0]).saldo_devedor, Decimal("0"))
            self.assertEqual(db.session.get(Venda, self.ids[1]).saldo_devedor, Decimal("120"))
            mov = Caixa.query.all()
            self.assertEqual(len(mov), 1)
            self.assertEqual(mov[0].valor, Decimal("93"))
            self.assertEqual(mov[0].descricao, f"Pagamento de fiado - Venda #{self.ids[0]}")
        self.post(f"/receber/{self.ids[0]}", {
            "modo": "total", "saldo_conferido": "93.00"
        })
        with app.app_context():
            self.assertEqual(Caixa.query.count(), 1)

    def test_quitar_cliente_registra_todos_sem_afetar_outros(self):
        res = self.post(f"/receber-cliente/{self.cliente_id}", {
            "saldo_conferido": "213.00"
        })
        self.assertEqual(res.status_code, 302)
        with app.app_context():
            self.assertEqual(db.session.get(Venda, self.ids[0]).pago, Decimal("93"))
            self.assertEqual(db.session.get(Venda, self.ids[1]).pago, Decimal("170"))
            self.assertEqual(db.session.get(Venda, self.ids[2]).saldo_devedor, Decimal("80"))
            self.assertEqual(db.session.get(Venda, self.ids[3]).pago, Decimal("40"))
            self.assertEqual(Caixa.query.count(), 2)
            self.assertEqual(sum((m.valor for m in Caixa.query.all()), Decimal("0")), Decimal("213"))
            self.assertEqual(set(m.descricao for m in Caixa.query.all()), {
                f"Pagamento de fiado - Venda #{self.ids[0]}",
                f"Pagamento de fiado - Venda #{self.ids[1]}",
            })

        self.post(f"/receber-cliente/{self.cliente_id}", {
            "saldo_conferido": "213.00"
        })
        with app.app_context():
            self.assertEqual(Caixa.query.count(), 2)

    def test_valor_de_tela_desatualizada_e_bloqueado(self):
        self.post(f"/receber/{self.ids[0]}", {
            "modo": "total", "saldo_conferido": "90.00"
        })
        self.post(f"/receber-cliente/{self.cliente_id}", {
            "saldo_conferido": "263.00"
        })
        with app.app_context():
            self.assertEqual(Caixa.query.count(), 0)
            self.assertEqual(db.session.get(Venda, self.ids[0]).pago, Decimal("0"))
            self.assertEqual(db.session.get(Venda, self.ids[1]).pago, Decimal("50"))

    def test_parcial_continua_funcionando(self):
        self.post(f"/receber/{self.ids[0]}", {
            "modo": "parcial", "valor": "10.50"
        })
        with app.app_context():
            self.assertEqual(db.session.get(Venda, self.ids[0]).pago, Decimal("10.50"))
            self.assertEqual(Caixa.query.one().valor, Decimal("10.50"))

    def test_nao_recebe_venda_excluida(self):
        with app.app_context():
            db.session.add(ExclusaoVenda(venda_id=self.ids[0]))
            db.session.commit()
        self.post(f"/receber-cliente/{self.cliente_id}", {
            "saldo_conferido": "120.00"
        })
        with app.app_context():
            self.assertEqual(Caixa.query.count(), 1)
            self.assertEqual(Caixa.query.one().valor, Decimal("120"))
            self.assertEqual(db.session.get(Venda, self.ids[0]).pago, Decimal("0"))

    def test_requer_csrf(self):
        res = self.client.post(f"/receber-cliente/{self.cliente_id}", data={
            "saldo_conferido": "213.00"
        })
        self.assertEqual(res.status_code, 400)
        with app.app_context():
            self.assertEqual(Caixa.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
