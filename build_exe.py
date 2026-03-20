import PyInstaller.__main__
import sys

def build():
    options = [
        'main.py',
        '--name=MySafetyReport',
        '--icon=logo.ico',
        '--clean',
        # Add Jinja2 templates and Static files
        '--add-data=web/templates;web/templates',
        '--add-data=web/static;web/static',
        # Include hidden imports for dynamic loading frameworks
        '--hidden-import=uvicorn',
        '--hidden-import=fastapi',
        '--hidden-import=pandas',
        '--hidden-import=sqlalchemy',
        '--hidden-import=websockets',
        '--hidden-import=bs4',
        '--hidden-import=selenium',
        '--hidden-import=gspread',
        # Show console for server logs, or use --windowed to hide it entirely (browser pop up only)
        # '--windowed'
    ]
    
    # On linux separator is ':', on windows it's ';'
    if sys.platform != "win32":
        options[4] = '--add-data=web/templates:web/templates'
        options[5] = '--add-data=web/static:web/static'

    PyInstaller.__main__.run(options)

if __name__ == "__main__":
    build()
