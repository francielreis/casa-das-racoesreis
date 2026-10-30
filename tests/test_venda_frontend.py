"""Smoke tests for the sale form: actual rendered JavaScript, no production DB writes."""
import os
import re
import shutil
import subprocess
import unittest

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-only-secret"
os.environ["ADMIN_USER"] = "auditor"
os.environ["ADMIN_PASSWORD"] = "senha-testes"

from app import app, db, Produto
from decimal import Decimal


class TelaVendaTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        with app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(Produto(
                nome="Torta de algodão", unidade="saco", categoria="Ração",
                estoque=Decimal("40"), custo=Decimal("60.00"),
                preco=Decimal("93.00"), estoque_minimo=Decimal("1")
            ))
            db.session.commit()
        self.client = app.test_client()
        self.client.post("/login", data={"usuario": "auditor", "senha": "senha-testes"})

    def rendered_javascript(self):
        resposta = self.client.get("/venda")
        self.assertEqual(resposta.status_code, 200)
        html = resposta.get_data(as_text=True)
        self.assertIn('Pesquisar produto pelo nome', html)
        self.assertIn('Estoque: 40 saco', html)
        self.assertIn('Torta de algodão', html)
        self.assertIn('Produtos disponíveis (1)', html)
        self.assertIn('class="produto-escolha"', html)
        self.assertIn('data-produto-id="1"', html)
        self.assertIn('value="1" selected', html)
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
        self.assertTrue(scripts)
        return scripts[-1]

    @unittest.skipUnless(shutil.which("node"), "Node.js indisponível")
    def test_javascript_renderizado_sem_erro_de_sintaxe(self):
        js = self.rendered_javascript()
        resultado = subprocess.run(
            ["node", "--check"], input=js, text=True,
            capture_output=True, check=False
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js indisponível")
    def test_subtotal_ao_selecionar_torta_e_quantidade_um(self):
        js = self.rendered_javascript()
        # DOM pequeno para testar a rotina real sem navegadores externos.
        setup = r"""
const noop = () => {};
const opcao = {
    value: "1",
    textContent: "Torta de algodão — R$ 93,00 | Estoque: 40 saco",
    dataset: {preco: "93.00", estoque: "40", unidade: "saco"},
    hidden: false
};
const select = {value:"1", selectedIndex:0, options:[opcao], addEventListener:noop};
const quantidade = {value:"1", addEventListener:noop};
const detalhes = {textContent:""};
const subtotal = {textContent:""};
const busca = {value:"", addEventListener:noop};
const remover = {addEventListener:noop};
const painel = {open:true};
const nenhum = {hidden:true};
const produtoNome = {textContent:"Torta de algodão"};
const botao = {
    dataset:{produtoId:"1"},
    hidden:false,
    querySelector:(x)=>x===".produto-nome" ? produtoNome : null,
    addEventListener:(name, fn)=>{ if(name==="click") botao.click=fn; }
};
const linha = {
 querySelector(x) {return ({
 ".produto-venda":select, ".quantidade-venda":quantidade,
 ".detalhes-produto":detalhes, ".subtotal-item":subtotal,
 ".busca-produto":busca, ".remover-item":remover,
 ".produtos-disponiveis":painel, ".nenhum-resultado":nenhum
 })[x];},
 querySelectorAll:(x)=>x===".produto-escolha" ? [botao] : []
};
const itens = {querySelectorAll:()=>[linha],querySelector:()=>linha};
const total={textContent:""};
const tipo={value:"avista",addEventListener:noop};
const areaEntrada={hidden:true};
const modelo={content:{}};
const adicionar={addEventListener:noop};
const form={addEventListener:noop};
const elementos={
 "itens-venda":itens, "modelo-item":modelo, "adicionar-item":adicionar,
 "total-compra":total, "tipo-venda":tipo,
 "area-entrada":areaEntrada, "form-venda":form
};
const document={getElementById:(id)=>elementos[id]};
const window={confirm:()=>true};
"""
        verificar = r"""
if (total.textContent.replace(/\s/g, " ") !== "R$ 93,00") throw Error("Total inválido: "+total.textContent);
if (subtotal.textContent.replace(/\s/g, " ") !== "Subtotal: R$ 93,00")
    throw Error("Subtotal inválido: "+subtotal.textContent);
if (!detalhes.textContent.includes("Estoque: 40"))
    throw Error("Estoque não aparece: "+detalhes.textContent);
if(typeof botao.click !== "function") throw Error("Clique não foi ligado ao produto");
botao.click();
if(painel.open !== false) throw Error("Lista não fechou após escolher produto");
busca.value = "torta";
if(!botao.hidden) throw Error("Produto encontrado foi ocultado");
"""
        resultado = subprocess.run(
            ["node", "-"], input=setup + js + verificar, text=True,
            capture_output=True, check=False
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


if __name__ == "__main__":
    unittest.main()
