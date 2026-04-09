from html.parser import HTMLParser
class MyHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
    def handle_starttag(self, tag, attrs):
        if tag == "div":
            _id = next((v for k,v in attrs if k=='id'), '')
            _class = next((v for k,v in attrs if k=='class'), '')
            self.stack.append((tag, self.getpos()[0], _id, _class))
    def handle_endtag(self, tag):
        if tag == "div":
            if self.stack:
                self.stack.pop()
            else:
                print(f"Extra closing div at line {self.getpos()[0]}")
    def close(self):
        super().close()
        for tag, line, _id, _class in self.stack:
            print(f"Unclosed div at line {line}: id='{_id}', class='{_class}'")

parser = MyHTMLParser()
parser.feed(open('static/index.html').read())
parser.close()
