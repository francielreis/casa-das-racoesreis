"""Gera relatorios administrativos em PDF sem modificar o banco de dados."""
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle
)


COR_VERDE = colors.HexColor("#176b3a")
COR_CINZA = colors.HexColor("#5b645d")
COR_ALTERNADA = colors.HexColor("#f3f7f4")


def gerar_pdf_relatorio(periodo, inicio_local, fim_local, vendas, movimentos,
                        produtos_agrupados, total, lucro, entradas, saidas,
                        fiado_atual, formata_dinheiro, formata_quantidade,
                        data_local, fuso_loja):
    """Exporta exatamente o periodo e registros ja usados na tela /relatorios."""
    buffer = BytesIO()
    largura, altura = landscape(A4)
    margem = 13 * mm
    disponivel = largura - 2 * margem
    documento = SimpleDocTemplate(
        buffer,
        pagesize=(largura, altura),
        topMargin=19 * mm, bottomMargin=16 * mm,
        leftMargin=margem, rightMargin=margem,
        title="Relatorios - Casa das Racoes Reis",
        author="Casa das Racoes Reis",
    )
    estilos = getSampleStyleSheet()
    estilos.add(ParagraphStyle(
        name="RacaoTitulo", parent=estilos["Heading1"], fontSize=17, leading=21,
        textColor=COR_VERDE, spaceAfter=6
    ))
    estilos.add(ParagraphStyle(
        name="RacaoSecao", parent=estilos["Heading2"], fontSize=11, leading=14,
        textColor=COR_VERDE, spaceBefore=13, spaceAfter=6
    ))
    estilos.add(ParagraphStyle(
        name="RacaoTexto", parent=estilos["Normal"], fontSize=8.3,
        leading=12, spaceAfter=5
    ))
    estilos.add(ParagraphStyle(
        name="RacaoCelula", parent=estilos["Normal"], fontSize=7,
        leading=9, spaceAfter=0, splitLongWords=True
    ))
    estilos.add(ParagraphStyle(
        name="RacaoCabecalho", parent=estilos["RacaoCelula"],
        fontName="Helvetica-Bold", textColor=colors.white
    ))
    estilos.add(ParagraphStyle(
        name="RacaoDinheiro", parent=estilos["RacaoCelula"], alignment=TA_RIGHT
    ))
    estilos.add(ParagraphStyle(
        name="RacaoDestaque", parent=estilos["Normal"], fontSize=11,
        leading=15, textColor=COR_VERDE, spaceAfter=7
    ))

    def texto(valor, estilo="RacaoCelula"):
        return Paragraph(
            escape(str(valor if valor is not None else "")),
            estilos[estilo],
        )

    def tabela(linhas, larguras):
        tab = LongTable(linhas, colWidths=larguras, repeatRows=1, splitByRow=1)
        tab.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COR_VERDE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_ALTERNADA]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return tab

    periodo_label = {
        "diario": "Diário",
        "semanal": "Semanal",
        "mensal": "Mensal",
        "personalizado": "Personalizado",
    }[periodo]
    ate = fim_local - timedelta(days=1)
    data_inicio = inicio_local.strftime("%d/%m/%Y")
    data_fim = ate.strftime("%d/%m/%Y")

    conteudo = [
        texto("CASA DAS RAÇÕES REIS", "RacaoTitulo"),
        texto("Relatório financeiro " + periodo_label + " | " + data_inicio
              + " a " + data_fim, "RacaoDestaque"),
        texto("Emitido em " + datetime.now(fuso_loja).strftime("%d/%m/%Y às %H:%M")
              + " | Horário do Piauí", "RacaoTexto"),
        Spacer(1, 6),
    ]
    resumo = [
        [texto("Indicador", "RacaoCabecalho"),
         texto("Valor", "RacaoCabecalho")],
        [texto("Vendas do período"), texto(formata_dinheiro(total), "RacaoDinheiro")],
        [texto("Lucro bruto das vendas"), texto(formata_dinheiro(lucro), "RacaoDinheiro")],
        [texto("Entradas de caixa"), texto(formata_dinheiro(entradas), "RacaoDinheiro")],
        [texto("Saídas de caixa"), texto(formata_dinheiro(saidas), "RacaoDinheiro")],
        [texto("Movimentação líquida (entradas - saídas)"),
         texto(formata_dinheiro(entradas - saidas), "RacaoDinheiro")],
        [texto("Fiado ainda pendente das vendas do período"),
         texto(formata_dinheiro(fiado_atual), "RacaoDinheiro")],
        [texto("Itens vendidos"), texto(len(vendas), "RacaoDinheiro")],
    ]
    conteudo.append(tabela(resumo, [disponivel * .66, disponivel * .34]))
    conteudo.append(Spacer(1, 7))
    conteudo.append(texto(
        "Fiado: saldo atual das vendas realizadas neste período, inclusive após "
        "recebimentos posteriores. Entradas de caixa incluem recebimentos de "
        "vendas antigas e lançamentos manuais. Lucro bruto não desconta despesas. "
        "Movimentação líquida não equivale ao saldo acumulado em caixa.",
        "RacaoTexto"
    ))

    if periodo in ("mensal", "personalizado"):
        conteudo.append(texto("Resumo por produto", "RacaoSecao"))
        colunas = (
            "Produto", "Unidade", "Quantidade", "Faturamento", "Lucro bruto", "Itens vendidos"
        )
        linhas = [[texto(c, "RacaoCabecalho") for c in colunas]]
        for item in produtos_agrupados:
            linhas.append([
                texto(item["produto"]),
                texto(item["unidade"]),
                texto(formata_quantidade(item["quantidade"])),
                texto(formata_dinheiro(item["faturamento"]), "RacaoDinheiro"),
                texto(formata_dinheiro(item["lucro"]), "RacaoDinheiro"),
                texto(item["vendas"]),
            ])
        if not produtos_agrupados:
            linhas.append([texto("Nenhum produto vendido"), "", "", "", "", ""])
        else:
            linhas.append([
                texto("TOTAL DO PERÍODO"), "", "",
                texto(formata_dinheiro(total), "RacaoDinheiro"),
                texto(formata_dinheiro(lucro), "RacaoDinheiro"),
                texto(len(vendas)),
            ])
        conteudo.append(tabela(linhas, [disponivel * x for x in
                                       (.30, .12, .14, .17, .16, .11)]))

    conteudo.append(texto("Vendas detalhadas", "RacaoSecao"))
    colunas = ("Data", "Cliente", "Produto", "Quantidade", "Total", "Lucro bruto", "Tipo")
    linhas = [[texto(c, "RacaoCabecalho") for c in colunas]]
    for v in vendas:
        linhas.append([
            texto(data_local(v.data).strftime("%d/%m/%Y %H:%M")),
            texto(v.cliente.nome if v.cliente else "Sem cliente"),
            texto(v.produto.nome if v.produto else "Produto não encontrado"),
            texto(formata_quantidade(v.quantidade) + " " +
                  (v.produto.unidade if v.produto else "")),
            texto(formata_dinheiro(v.total), "RacaoDinheiro"),
            texto(formata_dinheiro(v.lucro), "RacaoDinheiro"),
            texto("Fiado" if v.tipo == "fiado" else "À vista"),
        ])
    if not vendas:
        linhas.append([texto("Nenhuma venda no período"), "", "", "", "", "", ""])
    conteudo.append(tabela(linhas, [disponivel * x for x in
                                    (.125, .20, .20, .13, .12, .125, .10)]))

    conteudo.append(texto("Movimentações de caixa", "RacaoSecao"))
    linhas = [[texto(c, "RacaoCabecalho")
               for c in ("Data", "Descrição", "Tipo", "Valor")]]
    for m in movimentos:
        linhas.append([
            texto(data_local(m.data).strftime("%d/%m/%Y %H:%M")),
            texto(m.descricao),
            texto("Entrada" if m.tipo == "entrada" else "Saída"),
            texto(formata_dinheiro(m.valor), "RacaoDinheiro"),
        ])
    if not movimentos:
        linhas.append([texto("Nenhuma movimentação no período"), "", "", ""])
    conteudo.append(tabela(linhas, [disponivel * x for x in
                                    (.18, .51, .13, .18)]))

    def rodape(c, d):
        c.saveState()
        c.setStrokeColor(colors.HexColor("#d2ddd5"))
        c.line(margem, 13 * mm, largura - margem, 13 * mm)
        c.setFont("Helvetica", 8)
        c.setFillColor(COR_CINZA)
        c.drawString(margem, 8 * mm, "Casa das Rações Reis | Relatório interno")
        c.drawRightString(largura - margem, 8 * mm, "Página " + str(d.page))
        c.restoreState()

    documento.build(conteudo, onFirstPage=rodape, onLaterPages=rodape)
    return buffer.getvalue()
