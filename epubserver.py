import argparse
import bs4
import epubfile
import flask; from flask import request
import hashlib
import jinja2
import mimetypes
import sys

from voussoirkit import betterhelp
from voussoirkit import cacheclass
from voussoirkit import pathclass
from voussoirkit import vlogging

log = vlogging.get_logger(__name__, 'epubserver')

books = cacheclass.Cache(maxlen=100)

site = flask.Flask(__name__)

def md5_string(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()

def navpoint_to_li(soup, navpoint, srcbase, depth):
    li = soup.new_tag('li')
    a = soup.new_tag('a')
    a['href'] = '/' + srcbase.join(navpoint.content['src']).absolute_path
    a['class'] = f'toc_depth_{depth}'
    a['data-depth'] = str(depth)
    a.string = navpoint.navLabel.text.strip()
    children = list(navpoint.find_all('navPoint', recursive=False))
    if children:
        details = soup.new_tag('details')
        summary = soup.new_tag('summary')
        summary.append(a)
        details.append(summary)
        li.append(details)
        ol = soup.new_tag('ol')
        ol['class'] = f'toc_depth_{depth+1}'
        for navpoint in children:
            ol.append(navpoint_to_li(soup, navpoint, srcbase, depth+1))
        details.append(ol)
    else:
        li.append(a)
    return li

def prepare_reader(book):
    reader = bs4.BeautifulSoup('''
    <!DOCTYPE html>
    <html id="reader_html">
    <head>
    <style>
    #reader_html, #reader_html > body
    {
        margin: 0;
        height: 100vh;
        width: 100vw;
        box-sizing:border-box;
    }
    *
    {
        box-sizing: inherit;
    }

    body
    {
        position: relative;
    }
    #table_of_contents
    {
        position: absolute;
        overflow-y:auto;
        left:0;
        top:0;
        bottom:0;
        width:400px;
        background-color: lightgray;
    }
    #table_of_contents .current_chapter
    {
        font-weight: bold;
    }
    #iframe_holder
    {
        position:fixed;
        top:0;
        bottom:0;
        right:0;
        left:400px;
    }
    #reader_iframe
    {
        width: 100%;
        height:100%;
        margin:0;
        border:0;
    }
    </style>
    </head>
    <body>
    <section id="table_of_contents">
    <ol>
    </ol>
    </section>
    <div id="iframe_holder"><iframe id="reader_iframe"></iframe></div>
    </body>

    <script>
    const TOC = document.getElementById("table_of_contents");
    const IFRAME = document.getElementById("reader_iframe");
    const BOOK_ID = "{BOOK_ID}";
    let current_chapter = null;

    function toc_unbold_all()
    {
        current_chapter = null;
        for (const a of TOC.getElementsByTagName("a"))
        {
            a.classList.remove("current_chapter")
        }
    }
    function toc_identify_current_chapter()
    {
        toc_unbold_all();
        for (const a of TOC.getElementsByTagName("a"))
        {
            if (a.href == IFRAME.src)
            {
                current_chapter = a;
                a.classList.add("current_chapter");
                let x = a;
                while (x !== null)
                {
                    const details = x.parentElement.closest("details");
                    if (details !== null)
                    {
                        details.open = true;
                    }
                    x = details;
                }
            }
        }
    }

    function toc_onclick(event)
    {
        if (event.which !== 1)
        {
            return;
        }
        if (event.target.tagName === "A")
        {
            IFRAME.src = event.target.href;
            localStorage.setItem(BOOK_ID + ".leftoff_page", event.target.href);
            if (current_chapter !== null)
            {
                current_chapter.classList.remove("current_chapter");
                event.target.classList.add("current_chapter");
            }
            current_chapter = event.target;
            event.preventDefault();
            event.stopPropagation();
        }
    }
    function on_pageload()
    {
        TOC.addEventListener("click", toc_onclick);
        const leftoff_page = (localStorage.getItem(BOOK_ID + ".leftoff_page") || null);
        if (leftoff_page !== null)
        {
            IFRAME.src = leftoff_page;
        }
        else
        {
            const first_page = TOC.querySelector("a").href;
            IFRAME.src = first_page;
            localStorage.setItem(BOOK_ID + ".leftoff_page", first_page);
        }
        toc_identify_current_chapter();
    }
    document.addEventListener("DOMContentLoaded", on_pageload);
    </script>
    </html>
    '''.replace('{BOOK_ID}', md5_string(book.root_directory.normcase)), 'html.parser')

    ncx_id = book.get_ncx()
    toc = reader.find('section', {'id': 'table_of_contents'})
    if ncx_id:
        ncx_filepath = book.get_filepath(ncx_id)
        ncx = book.read_file(ncx_id, soup=True)
        srcbase = ncx_filepath.parent
        for navpoint in list(ncx.navMap.find_all('navPoint', recursive=False)):
            toc.ol.append(navpoint_to_li(reader, navpoint, srcbase, depth=1))

    book._reader = reader

def get_book(epub_path):
    if epub_path in books:
        return books[epub_path]
    book = epubfile.Epub(epub_path, read_only=True)
    book._epubserver_manifest = book.get_texts(soup=True, skip_nav=True)
    print(book._epubserver_manifest)
    book._epubserver_manifest_ids = [x['id'] for x in book._epubserver_manifest]
    book._epubserver_reverse_manifest = {
        book.get_filepath(item['id']): item
        for item in book._epubserver_manifest
    }
    prepare_reader(book)
    books[epub_path] = book
    return book

def epubserver_flask(port, *args, **kwargs):
    @site.route('/')
    def root():
        response = jinja2.Template('''
        <html>
        <body>
        </body>
        </html>
        ''').render()
        return response

    @site.route('/<path:path>')
    def mainroute(path):
        if path.endswith('.epub'):
            path += '/'

        try:
            ix = path.lower().index('.epub/')
        except ValueError:
            return flask.abort(404, 'URL does not contain epub.')
        else:
            epub_path = path[:ix+5]
            request_path = path[ix+6:]

        try:
            book = get_book(epub_path)
        except FileNotFoundError:
            return flask.abort(404, 'Could not open book.')

        if request_path == '':
            return str(book._reader)

        if request_path in book._epubserver_manifest:
            return book.read_file(request_path)

        filepath = book.root_directory.join(request_path)
        if filepath not in book.root_directory:
            return flask.abort(404)

        try:
            log.debug('Trying filepath %s', filepath)
            content = book._fopen(filepath, 'rb').read()
        except Exception:
            return flask.abort(404)

        manifest_item = book._epubserver_reverse_manifest.get(filepath)
        if manifest_item is not None:
            mime = manifest_item['media-type']
        else:
            mime = mimetypes.guess_type(request_path)[0]

        response = flask.make_response(content)
        if mime:
            response.headers['Content-Type'] = mime
        else:
            response.headers['Content-Type'] = None

        response.headers['Cache-Control'] = 'max-age=600'

        return response

    site.run(host='0.0.0.0', port=port)

def epubserver_argparse(args):
    epubserver_flask(args.port)
    return 0

@vlogging.main_decorator
def main(argv):
    parser = argparse.ArgumentParser(
        description='''
        ''',
    )
    parser.add_argument(
        'port',
        type=int,
    )
    parser.set_defaults(func=epubserver_argparse)

    return betterhelp.go(parser, argv)

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
