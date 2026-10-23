"""공용 DB 전체 SQLite 사본을 만드는 대화형 CLI. 비밀번호를 파일에 저장하지 않는다."""
import argparse
import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parents[1]))
from reviewlab.cloud.client import SupabaseClient,CloudError
from reviewlab.cloud.library import CloudLibrary


def main():
    parser=argparse.ArgumentParser(description='인증 후 ReviewLab 공용 전체 백업')
    parser.add_argument('--workspace',required=True,help='앱에서 확인한 작업공간 UUID')
    parser.add_argument('--mode',choices=('actual','example'),required=True)
    parser.add_argument('--output-root',required=True,help='안전한 백업 루트 폴더')
    args=parser.parse_args()
    client=None;library=None
    try:
        client=SupabaseClient(os.environ.get('REVIEWLAB_SUPABASE_URL',''),os.environ.get('REVIEWLAB_SUPABASE_KEY',''))
        client.sign_in(input('Supabase 이메일: ').strip(),getpass.getpass('Supabase 비밀번호: '))
        library=CloudLibrary(client,args.workspace,args.mode,Path(args.output_root))
        print('전체 SQLite 백업:',library.snapshot())
        return 0
    except (ValueError,OSError):
        print('백업을 완료하지 못했습니다. 인증·권한·설정·저장 폴더를 확인하세요.',file=sys.stderr)
        return 1
    finally:
        if library:library.close()
        if client:
            try:client.logout()
            except CloudError:pass


if __name__=='__main__':sys.exit(main())
