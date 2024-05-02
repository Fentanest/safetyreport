from selenium import webdriver
from sqlalchemy import create_engine, text
import os
import sys
import settings.settings as settings
import driv
import login
import crawltitle
import crawldetail
import postcrawling
import items

# 설정 변수 리스트
variables_to_check = [
    ("username", "username 미지정"),
    ("password", "password 미지정"),
    ("google_sheet_key", "sheetkey 미지정"),
    ("remotepath", "원격 셀레니움 미지정")
]

# 설정 변수 확인
for var_name, error_message in variables_to_check:
    if getattr(settings, var_name) in ["nousername", "nopassword", "nosheetkey", "nonpath"]:
        print(error_message)
        sys.exit(1)
    else:
        print("변수 값 확인")
        pass

# 로그 폴더 있는지 확인
if os.path.exists(settings.logpath) == False:
    print("로그파일 저장폴더를 생성합니다.")
    os.mkdir(settings.logpath)
else:
    print("로그파일 저장폴더 있음")

# 로그 열기
sys.stdout = open(os.path.join(settings.logpath, settings.stdout), 'w') # 평시 파일
sys.stderr = open(os.path.join(settings.logpath, settings.stderr), 'w') # 에러 파일

# 결과저장 폴더 있는지 확인
if os.path.exists(settings.path) == False:
    print("결과파일 저장폴더를 생성합니다.")
    os.mkdir(settings.path) # 없으면 생성
else:
    print("결과파일 저장폴더 있음")

# DB준비
engine = create_engine(f'sqlite:///{os.path.join(settings.path, settings.db)}')
with engine.connect() as conn:
    check_query = text("SELECT name FROM sqlite_master WHERE type='table' AND name='STATUS'") # 초기생성자 확인
    result = conn.execute(check_query)
    table_exists = result.fetchone() is not None
    print("초기생성자 여부: ", table_exists)
    if table_exists == True: # 초기생성자 있으면 패스
        postcrawling.drop_title_temp(engine=engine, conn=conn)
        postcrawling.drop_detail_temp(engine=engine, conn=conn)
        postcrawling.drop_merge_temp(engine=engine, conn=conn)
    else:  # 초기생성자 없으면 테이블 초기화 시작
        makecheck = text('CREATE TABLE STATUS (INITIALIZED);')
        droptitle = text(f'DROP TABLE IF EXISTS {settings.table_title};')
        dropdetail = text(f'DROP TABLE IF EXISTS {settings.table_detail};')
        createtitle = text(f'''    
            CREATE TABLE {settings.table_title}
            (
                ID INT PRIMARY KEY,
                상태 TEXT,
                신고번호 TEXT,
                신고명 TEXT,            
                신고일 date
            );''')
        createdetail = text(f'''
            CREATE TABLE {settings.table_detail}
            (
                ID INT PRIMARY KEY,
                처리상태 TEXT,
                차량번호 TEXT,
                위반법규 TEXT,
                범칙금_과태료 TEXT,
                벌점 TEXT,
                처리기관 TEXT,
                담당자 TEXT,
                답변일 date,
                발생일자 date,
                발생시각 TIME,
                위반장소 TEXT,
                종결여부 TEXT
            );''')
        createmerge = text(f'''
            CREATE TABLE {settings.table_merge}
            (
                ID INT PRIMARY KEY,
                상태 TEXT,
                신고번호 TEXT,
                신고명 TEXT,            
                신고일 date,
                처리상태 TEXT,
                차량번호 TEXT,
                위반법규 TEXT,
                범칙금_과태료 TEXT,
                벌점 TEXT,
                처리기관 TEXT,
                담당자 TEXT,
                답변일 date,
                발생일자 date,
                발생시각 TIME,
                위반장소 TEXT,
                종결여부 TEXT,
                공개결과 TEXT
            );''')
        createopendata = text(f'''
            CREATE TABLE {settings.table_opendata}
            (
                ID INT PRIMARY KEY,
                신고번호 TEXT,
                공개결과 TEXT
            );''')
        conn.execute(makecheck)
        print("초기생성자 설정")
        conn.execute(droptitle)
        conn.execute(dropdetail)
        conn.execute(createtitle)
        conn.execute(createdetail)
        conn.execute(createopendata)
        print("최초사용 설정 완료")
        conn.commit() # 변경사항 반영

# 크롬 열기
driver = driv.create_driver()

# 로그인
login.login_mysafety(driver=driver)

# 게시판 리스트 크롤링, 개별 ID 확보 후 temp테이블과 병합
titlelist = crawltitle.Crawling_title(driver=driver)
items.title_to_sql(dataframes=titlelist, engine=engine, conn=conn)
postcrawling.merge_title(engine=engine, conn=conn)
postcrawling.drop_title_temp(engine=engine, conn=conn)

# 개별 신고 건 크롤링, 상태 저장 후 temp테이블과 병합
detaillist = items.get_cNo(engine=engine, conn=conn)
detail_datas = crawldetail.Crawling_detail(driver=driver, list=detaillist)

items.deatil_to_sql(dataframes=detail_datas, engine=engine, conn=conn)
postcrawling.merge_detail(engine=engine, conn=conn)
postcrawling.drop_detail_temp(engine=engine, conn=conn)

# 스프레드시트의 정보공개청구 결과 긁어와서 최종 병합
items.opendata_from_gc(engine=engine, conn=conn) # 정보공개청구
items.merge_from_sql(engine=engine, conn=conn)
postcrawling.merge_final(engine=engine, conn=conn)
postcrawling.drop_merge_temp(engine=engine, conn=conn)
df = items.load_results(engine=engine, conn=conn)
items.save_results(df=df)

# 로그 닫기
sys.stdout.close()
sys.stderr.close()