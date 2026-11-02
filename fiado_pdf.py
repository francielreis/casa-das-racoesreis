"""Gera relatorio de auditoria do fiado da loja, somente leitura."""
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, LongTable, Table, TableStyle
)


VERDE = colors.HexColor("#176b3a")
CINZA_CLARO = colors.HexColor("#e9efeb")
CINZA_TEXTO = colors.HexColor("#505b55")


def gerar_pdf_fiado(clientes, grupo_por_venda, total_fiado, total_pago,
                    fiado_antigo, fiado_atual, alertas, formata_dinheiro,
                    data_local, fuso_loja):
    """Retorna PDF em bytes. O banco de dados nao e modificado."""
    buffer = BytesIO()
    largura, altura = landscape(A4)
    margem = 12 * mm
    largura_util = largura - 2 * margem
    doc = SimpleDocTemplate(
        buffer, pagesize=(largura, altura),
        leftMargin=margem, rightMargin=margem,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title="Auditoria de fiados - Casa das Racoes Reis",
        author="Casa das Racoes Reis",
    )

    estilos = getSampleStyleSheet()
    estilos.add(ParagraphStyle(
        name="TituloLojaFiado", parent=estilos["Heading1"], fontSize=17,
        leading=21, textColor=VERDE, spaceAfter=5
    ))
    estilos.add(ParagraphStyle(
        name="SubtituloFiado", parent=estilos["Heading2"], fontSize=12,
        leading=15, textColor=VERDE, spaceBefore=13, spaceAfter=7
    ))
    estilos.add(ParagraphStyle(
        name="TextoFiado", parent=estilos["Normal"], fontSize=9,
        leading=13, spaceAfter=5
    ))
    estilos.add(ParagraphStyle(
        name="CelulaFiado", parent=estilos["Normal"], fontSize=7.2,
        leading=9, spaceAfter=0
    ))
    estilos.add(ParagraphStyle(
        name="CabecalhoFiado", parent=estilos["Normal"], fontName="Helvetica-Bold",
        fontSize=7.6, leading=10, textColor=colors.white
    ))
    estilos.add(ParagraphStyle(
        name="CelulaDinheiroFiado", parent=estilos["CelulaFiado"], alignment=TA_RIGHT
    ))
    estilos.add(ParagraphStyle(
        name="DestaqueFiado", parent=estilos["Normal"], fontSize=9.2,
        leading=13, textColor=VERDE
    ))

    def p(valor, estilo="CelulaFiado"):
        return Paragraph(escape(str(valor if valor is not None else "")), estilos[estilo])

    def tabela(dados, larguras, titulo_header=True):
        t = LongTable(dados, colWidths=larguras, repeatRows=1 if titulo_header else 0,
                      hAlign="LEFT", splitByRow=1)
        comandos = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, -1), (-1, -1), 0.3, colors.HexColor("#d6ded8")),
        ]
        if titulo_header:
            comandos += [
                ("BACKGROUND", (0, 0), (-1, 0), VERDE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#f4f7f5")]),
            ]
        t.setStyle(TableStyle(comandos))
        return t

    gerado = datetime.now(fuso_loja).strftime("%d/%m/%Y as %H:%M")
    saldo_total = fiado_antigo + fiado_atual
    soma_clientes = sum((c["saldo"] for c in clientes), start=0)
    devedores = sum(1 for c in clientes if c["saldo"] > 0)

    story = [
        p("CASA DAS RAÇÕES REIS", "TituloLojaFiado"),
        p("Relatório de auditoria de fiados - uso interno", "DestaqueFiado"),
        p("Emissão: " + gerado + " | Todos os períodos, inclusive anteriores ao zeramento do caixa.", "TextoFiado"),
        Spacer(1, 7),
    ]
    resumo = [
        [p("SALDO A RECEBER", "CabecalhoFiado"),
         p("CLIENTES DEVEDORES", "CabecalhoFiado"),
         p("FIADO ANTERIOR AO ZERAMENTO", "CabecalhoFiado"),
         p("FIADO DO CAIXA ATUAL", "CabecalhoFiado")],
        [p(formata_dinheiro(saldo_total), "DestaqueFiado"),
         p(devedores, "DestaqueFiado"),
         p(formata_dinheiro(fiado_antigo), "DestaqueFiado"),
         p(formata_dinheiro(fiado_atual), "DestaqueFiado")],
    ]
    story.append(tabela(resumo, [largura_util / 4] * 4))
    story.append(Spacer(1, 10))
    story.append(p(
        "Total vendido fiado (histórico válido): " + formata_dinheiro(total_fiado)
        + " | Já pago: " + formata_dinheiro(total_pago), "TextoFiado"
    ))
    status = "CONFERE" if soma_clientes == saldo_total else "DIVERGÊNCIA"
    story.append(p(
        "Conferência da soma dos saldos por cliente: " + formata_dinheiro(soma_clientes)
        + " | Total geral: " + formata_dinheiro(saldo_total)
        + " | Resultado: " + status, "DestaqueFiado"
    ))

    story.append(p("Resumo por cliente", "SubtituloFiado"))
    colunas_cliente = [largura_util * x for x in (0.33, 0.09, 0.19, 0.19, 0.20)]
    dados_cliente = [[p("Cliente", "CabecalhoFiado"), p("Compras", "CabecalhoFiado"),
                      p("Vendido fiado", "CabecalhoFiado"), p("Pago", "CabecalhoFiado"),
                      p("Falta pagar", "CabecalhoFiado")]]
    for c in clientes:
        dados_cliente.append([
            p(c["nome"] + (" (ID " + str(c["id"]) + ")" if c["id"] else "")),
            p(len(c["compras"])), p(formata_dinheiro(c["total"]), "CelulaDinheiroFiado"),
            p(formata_dinheiro(c["pago"]), "CelulaDinheiroFiado"),
            p(formata_dinheiro(c["saldo"]), "CelulaDinheiroFiado"),
        ])
    if not clientes:
        dados_cliente.append([p("Nenhuma venda fiada cadastrada"), "", "", "", ""])
    story.append(tabela(dados_cliente, colunas_cliente))

    story.append(p("Detalhamento das compras fiadas", "SubtituloFiado"))
    proporcoes = (0.055, 0.075, 0.105, 0.205, 0.185, 0.125, 0.125, 0.125)
    cabecalhos = ("Venda", "Compra", "Data", "Cliente", "Produto", "Valor", "Pago", "Pendente")
    dados_venda = [[p(s, "CabecalhoFiado") for s in cabecalhos]]
    for c in clientes:
        for v in c["itens"]:
            grupo_id = grupo_por_venda.get(v.id)
            dados_venda.append([
                p(v.id), p(grupo_id or "-"),
                p(data_local(v.data).strftime("%d/%m/%Y")),
                p(c["nome"]),
                p(v.produto.nome if v.produto else "Produto não encontrado"),
                p(formata_dinheiro(v.total), "CelulaDinheiroFiado"),
                p(formata_dinheiro(v.pago), "CelulaDinheiroFiado"),
                p(formata_dinheiro(v.saldo_devedor), "CelulaDinheiroFiado"),
            ])
    if len(dados_venda) == 1:
        dados_venda.append([p("Sem vendas"), "", "", "", "", "", "", ""])
    story.append(tabela(dados_venda, [largura_util * x for x in proporcoes]))

    story.append(p("Alertas da auditoria (" + str(len(alertas)) + ")", "SubtituloFiado"))
    if alertas:
        for idx, alerta in enumerate(alertas, 1):
            story.append(p(str(idx) + ". " + alerta, "TextoFiado"))
    else:
        story.append(p("Nenhum alerta encontrado nas verificações automáticas.", "TextoFiado"))
    story.append(Spacer(1, 8))
    story.append(p(
        "Aviso: a conciliação comprova a soma dos lançamentos existentes, mas "
        "não substitui conferir comprovantes de pagamento, estornos ou recebimentos "
        "realizados fora do sistema.", "TextoFiado"
    ))

    def rodape(canvas, doc_atual):
        canvas.saveState()
        canvas.setStrokeColor(CINZA_CLARO)
        canvas.line(margem, 13 * mm, largura - margem, 13 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(CINZA_TEXTO)
        canvas.drawString(margem, 8 * mm, "Casa das Rações Reis | Relatório interno de fiados")
        canvas.drawRightString(largura - margem, 8 * mm, "Página " + str(doc_atual.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=rodape, onLaterPages=rodape)
    return buffer.getvalue()
