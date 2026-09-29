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

    os.unlink(tmp_db.name)

    if falhas:
        print(f'\nFALHAS: {len(falhas)}')
        for rota, erro in falhas:
            print(f'  {rota}: {erro}')
        sys.exit(1)

    print('OK — nenhuma rota GET sem parametro falhou.')


if __name__ == '__main__':
    main()
