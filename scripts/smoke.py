"""Teste de fumaca: sobe o app com SQLite e bate em toda rota GET sem
parametro, verificando que nenhuma retorna 5xx ou lanca excecao.

Nao substitui uma suite de testes de verdade, mas pega regressao grosseira
(erro de import, view quebrada, template faltando) em segundos.

Uso:
    venv/bin/python scripts/smoke.py
"""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def main():
    tmp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp_db.close()

    os.chdir(RAIZ)
    os.environ.setdefault('SECRET_KEY', 'x' * 40)
    os.environ.setdefault('API_TOKEN', 'y' * 40)
    os.environ['DATABASE_URL'] = f'sqlite:///{tmp_db.name}'
    os.environ.setdefault('MAIL_USER', 'a@b.c')
    os.environ.setdefault('MAIL_PASSWORD', 'p')
    os.environ.setdefault('MAIL_FROM', 'a@b.c')
    os.environ.setdefault('RUN_SCHEDULER', '0')
    os.environ.setdefault('FLASK_DEBUG', '0')

    import app as m

    with m.app.app_context():
        m.db.create_all()

    client = m.app.test_client()
    rotas = [r for r in m.app.url_map.iter_rules()
             if 'GET' in r.methods and not r.arguments]

    print(f'rotas totais: {len(list(m.app.url_map.iter_rules()))} '
          f'| GET sem parametro: {len(rotas)}')

    falhas = []
    for r in rotas:
        try:
            resp = client.get(r.rule)
            if resp.status_code >= 500:
                falhas.append((r.rule, resp.status_code))
        except Exception as e:
            falhas.append((r.rule, repr(e)[:150]))

    falhas += _checar_senhas(m)
    falhas += _checar_config(m)
    falhas += _checar_assinatura(m)
    falhas += _checar_bloqueio(m, client)

    os.unlink(tmp_db.name)

    if falhas:
        print(f'\nFALHAS: {len(falhas)}')
        for alvo, erro in falhas:
            print(f'  {alvo}: {erro}')
        sys.exit(1)

    print('OK — rotas GET, contrato de senha e leitura/escrita de Configuracao.')


def _checar_bloqueio(m, client):
    """Confere o que cada camada faz em cada estado da assinatura."""
    from datetime import datetime, timedelta

    falhas = []

    def montar(slug, horas_vencido):
        """Cria tenant cuja assinatura venceu ha `horas_vencido` horas."""
        with m.app.app_context():
            tenant = m.Tenant(slug=slug, nome='Barbearia', email=f'{slug}@x.com',
                              senha='senha123', ativo=True, assinatura_ativa=True)
            m.db.session.add(tenant)
            m.db.session.flush()
            m.db.session.add(m.Assinatura(
                tenant_id=tenant.id, plano='mensal', valor_total=49, valor_mensal=49,
                status='ativo',
                vencimento=datetime.utcnow() - timedelta(hours=horas_vencido, seconds=1)))
            m.db.session.commit()
            return tenant.id

    def logar(tid):
        with client.session_transaction() as sessao:
            sessao.clear()
            sessao[m._SESSION_GESTAO_TENANT_ID] = tid

    casos = [
        # (slug, horas vencido, painel esperado, site esperado)
        ('emdia',    -240, 200, 200),  # faltam 10 dias
        ('carencia',   12, 200, 200),  # venceu ha 12h — passa, com aviso
        ('bloqueado',  30, 302, 402),  # venceu ha 30h — corta
    ]
    for slug, horas, painel_esperado, site_esperado in casos:
        tid = montar(slug, horas)
        logar(tid)

        painel = client.get('/gestao/')
        if painel.status_code != painel_esperado:
            falhas.append(('bloqueio',
                           f'{slug}: /gestao/ deu {painel.status_code}, '
                           f'esperado {painel_esperado}'))

        site = client.get(f'/{slug}')
        if site.status_code != site_esperado:
            falhas.append(('bloqueio',
                           f'{slug}: site publico deu {site.status_code}, '
                           f'esperado {site_esperado}'))

        # O aviso so aparece na carencia.
        if horas == 12:
            corpo = painel.get_data(as_text=True)
            if 'Assinatura vencida' not in corpo:
                falhas.append(('bloqueio', 'carencia: faltou o aviso no painel'))
        elif horas == -240:
            if 'Assinatura vencida' in painel.get_data(as_text=True):
                falhas.append(('bloqueio', 'em dia: aviso apareceu sem motivo'))

    # Quem esta bloqueado cai na tela de assinatura vencida, nao em loop.
    tid_bloqueado = montar('bloqueado2', 48)
    logar(tid_bloqueado)
    tela = client.get('/gestao/assinatura-vencida')
    if tela.status_code != 402:
        falhas.append(('bloqueio',
                       f'tela de vencida deu {tela.status_code}, esperado 402'))

    # Renovar tem de liberar tudo de novo.
    with m.app.app_context():
        tenant = m.db.session.get(m.Tenant, tid_bloqueado)
        assinatura = tenant.assinatura_atual
        assinatura.vencimento = datetime.utcnow() + timedelta(days=30)
        assinatura.status = 'ativo'
        tenant.assinatura_ativa = True
        m.db.session.commit()
    logar(tid_bloqueado)
    if client.get('/gestao/').status_code != 200:
        falhas.append(('bloqueio', 'renovar nao liberou o painel'))

    with client.session_transaction() as sessao:
        sessao.clear()
    return falhas


def _checar_assinatura(m):
    """Verifica a regra de carencia de 24h em volta do vencimento."""
    from datetime import datetime, timedelta

    falhas = []
    casos = [
        # (descricao, horas desde o vencimento, estado esperado)
        ('faltando 10 dias',     -240, m.Tenant.ASSINATURA_ATIVA),
        ('faltando 1 hora',        -1, m.Tenant.ASSINATURA_ATIVA),
        ('venceu ha 1 minuto',      0, m.Tenant.ASSINATURA_CARENCIA),
        ('venceu ha 23 horas',     23, m.Tenant.ASSINATURA_CARENCIA),
        ('venceu ha 25 horas',     25, m.Tenant.ASSINATURA_BLOQUEADA),
        ('venceu ha 10 dias',     240, m.Tenant.ASSINATURA_BLOQUEADA),
    ]

    with m.app.app_context():
        for i, (descricao, horas, esperado) in enumerate(casos):
            tenant = m.Tenant(slug=f'assin{i}', nome='X', email=f'a{i}@x.com',
                              senha='senha123', ativo=True, assinatura_ativa=True)
            m.db.session.add(tenant)
            m.db.session.flush()
            # 'horas' positivo = ja venceu; negativo = ainda vai vencer.
            vencimento = datetime.utcnow() - timedelta(hours=horas, seconds=1)
            m.db.session.add(m.Assinatura(
                tenant_id=tenant.id, plano='mensal', valor_total=49,
                valor_mensal=49, status='ativo', vencimento=vencimento))
            m.db.session.commit()

            obtido = tenant.estado_assinatura()
            if obtido != esperado:
                falhas.append(('Assinatura',
                               f'{descricao}: esperado {esperado}, obtido {obtido}'))

        # assinatura_ativa=False manda bloquear, mesmo dentro do prazo.
        desligado = m.Tenant(slug='desligado', nome='X', email='d@x.com',
                             senha='senha123', ativo=True, assinatura_ativa=False)
        m.db.session.add(desligado)
        m.db.session.flush()
        m.db.session.add(m.Assinatura(
            tenant_id=desligado.id, plano='mensal', valor_total=49, valor_mensal=49,
            status='ativo', vencimento=datetime.utcnow() + timedelta(days=30)))
        m.db.session.commit()
        if desligado.estado_assinatura() != m.Tenant.ASSINATURA_BLOQUEADA:
            falhas.append(('Assinatura', 'desligado pelo admin deveria bloquear'))

        # Ativado na mao pelo admin, sem assinatura nenhuma: liberado.
        manual = m.Tenant(slug='manual', nome='X', email='m@x.com',
                          senha='senha123', ativo=True, assinatura_ativa=True)
        m.db.session.add(manual)
        m.db.session.commit()
        if manual.estado_assinatura() != m.Tenant.ASSINATURA_ATIVA:
            falhas.append(('Assinatura', 'ativado pelo admin sem assinatura deveria liberar'))

    return falhas


def _checar_config(m):
    """Grava e le uma Configuracao — cobre o caminho de escrita, que as rotas
    GET nao exercitam."""
    falhas = []
    with m.app.app_context():
        try:
            m._gravar_config('chave_smoke', 'v1', tenant_id=1)
            m.db.session.commit()

            lido = m._obter_config('chave_smoke', tenant_id=1)
            if lido is None:
                falhas.append(('Configuracao', 'gravou mas nao releu'))
            elif lido.valor != 'v1':
                falhas.append(('Configuracao', f'valor lido {lido.valor!r} != v1'))
            elif lido.chave != '1:chave_smoke':
                falhas.append(('Configuracao', f'chave {lido.chave!r} sem prefixo'))

            # Segunda gravacao tem de atualizar, nao duplicar.
            m._gravar_config('chave_smoke', 'v2', tenant_id=1)
            m.db.session.commit()
            if m._obter_config('chave_smoke', tenant_id=1).valor != 'v2':
                falhas.append(('Configuracao', 'update nao sobrescreveu'))
        except Exception as e:
            falhas.append(('Configuracao', f'{type(e).__name__}: {e}'))
    return falhas


def _checar_senhas(m):
    """Verifica o contrato do SenhaMixin nos tres modelos que o usam."""
    falhas = []
    modelos = [
        ('Tenant', lambda: m.Tenant(slug='x', nome='X', email='x@x.com',
                                    senha='senha123')),
        ('Usuario', lambda: m.Usuario(nome='X', email='u@x.com', senha='senha123')),
        ('Funcionario', lambda: m.Funcionario(nome='X', senha='senha123')),
    ]
    with m.app.app_context():
        for nome, criar in modelos:
            obj = criar()

            # O hash nunca pode ser a senha em texto puro.
            if obj.password == 'senha123':
                falhas.append((nome, 'senha gravada em texto puro'))

            if not obj.conferir_senha('senha123'):
                falhas.append((nome, 'conferir_senha rejeitou a senha correta'))

            if obj.conferir_senha('errada123'):
                falhas.append((nome, 'conferir_senha aceitou senha errada'))

            # Ler .senha tem de falhar: o campo e escreve-so.
            try:
                obj.senha
                falhas.append((nome, 'leitura de .senha deveria falhar'))
            except AttributeError:
                pass

            # Senha curta tem de ser recusada antes de virar hash.
            try:
                obj.senha = '123'
                falhas.append((nome, 'aceitou senha com menos de 8 caracteres'))
            except ValueError:
                pass

    return falhas


if __name__ == '__main__':
    main()
