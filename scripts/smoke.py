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

    os.unlink(tmp_db.name)

    if falhas:
        print(f'\nFALHAS: {len(falhas)}')
        for alvo, erro in falhas:
            print(f'  {alvo}: {erro}')
        sys.exit(1)

    print('OK — rotas GET, contrato de senha e leitura/escrita de Configuracao.')


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
