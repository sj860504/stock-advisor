from flask import Flask, render_template_string, request, redirect, url_for
from datetime import datetime
import uuid

app = Flask(__name__)

# 🗄️ 데이터 저장소 (In-Memory)
# Antigravity 엔진이 실행되는 동안만 유지됩니다.
posts = []

# 🎨 모던 UI 템플릿
HTML_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Antigravity Board</title>
    <style>
        :root { --primary: #6C5CE7; --bg: #DFE6E9; --surface: #FFFFFF; --text: #2D3436; --danger: #FF7675; }
        body { font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif; background-color: var(--bg); color: var(--text); margin: 0; padding: 0; display: flex; justify-content: center; min-height: 100vh; }
        .container { width: 100%; max-width: 700px; padding: 40px 20px; }
        
        /* 헤더 스타일 */
        header { text-align: center; margin-bottom: 40px; }
        header h1 { margin: 0; font-size: 2.5rem; color: var(--primary); letter-spacing: -1px; }
        header p { margin-top: 10px; color: #636e72; }

        /* 입력 폼 스타일 */
        .card { background: var(--surface); border-radius: 16px; padding: 25px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); margin-bottom: 25px; transition: transform 0.2s; }
        .input-group { margin-bottom: 15px; }
        input, textarea { width: 100%; padding: 14px; border: 2px solid #eee; border-radius: 12px; font-size: 1rem; transition: border-color 0.3s; box-sizing: border-box; outline: none; }
        input:focus, textarea:focus { border-color: var(--primary); }
        textarea { resize: vertical; min-height: 100px; }
        
        button.submit-btn { width: 100%; background: var(--primary); color: white; border: none; padding: 16px; border-radius: 12px; font-size: 1.1rem; font-weight: bold; cursor: pointer; transition: background 0.3s, transform 0.1s; }
        button.submit-btn:hover { background: #5a4ad1; transform: translateY(-2px); }

        /* 게시글 목록 스타일 */
        .post-list { list-style: none; padding: 0; }
        .post-card { position: relative; background: var(--surface); border-radius: 16px; padding: 25px; margin-bottom: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.03); }
        .post-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 15px; }
        .post-title { font-size: 1.3rem; font-weight: bold; margin: 0; color: #2d3436; }
        .post-date { font-size: 0.85rem; color: #b2bec3; }
        .post-content { font-size: 1rem; line-height: 1.6; color: #636e72; white-space: pre-wrap; }
        
        /* 삭제 버튼 */
        .delete-btn { display: inline-block; margin-top: 15px; color: var(--danger); text-decoration: none; font-size: 0.9rem; font-weight: 600; cursor: pointer; background: none; border: none; padding: 0; }
        .delete-btn:hover { text-decoration: underline; }

        .empty-state { text-align: center; color: #b2bec3; padding: 40px; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Antigravity Board</h1>
            <p>중력을 거스르는 가벼운 소통 공간 🚀</p>
        </header>

        <!-- 글쓰기 영역 -->
        <div class="card">
            <form action="/add" method="post">
                <div class="input-group">
                    <input type="text" name="title" placeholder="제목을 입력하세요" required autocomplete="off">
                </div>
                <div class="input-group">
                    <textarea name="content" placeholder="어떤 이야기를 나누고 싶으신가요?" required></textarea>
                </div>
                <button type="submit" class="submit-btn">글 게시하기 ✨</button>
            </form>
        </div>

        <!-- 게시글 목록 -->
        <div class="post-list">
            {% for post in posts|reverse %}
            <div class="post-card">
                <div class="post-header">
                    <h3 class="post-title">{{ post.title }}</h3>
                    <span class="post-date">{{ post.created_at }}</span>
                </div>
                <div class="post-content">{{ post.content }}</div>
                <form action="{{ url_for('delete_post', post_id=post.id) }}" method="post" style="display:inline;">
                    <button type="submit" class="delete-btn">삭제하기</button>
                </form>
            </div>
            {% else %}
            <div class="empty-state">
                <p>아직 게시글이 없어요.<br>첫 번째 글을 작성해보세요!</p>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, posts=posts)

@app.route('/add', methods=['POST'])
def add_post():
    title = request.form.get('title')
    content = request.form.get('content')
    
    # 게시글 객체 생성
    new_post = {
        'id': str(uuid.uuid4()),  # 고유 ID 생성
        'title': title,
        'content': content,
        'created_at': datetime.now().strftime("%Y.%m.%d %H:%M")
    }
    
    posts.append(new_post)
    return redirect(url_for('index'))

@app.route('/delete/<post_id>', methods=['POST'])
def delete_post(post_id):
    global posts
    # 해당 ID를 가진 게시글을 제외하고 리스트를 재구성 (삭제 효과)
    posts = [post for post in posts if post['id'] != post_id]
    return redirect(url_for('index'))

if __name__ == '__main__':
    print("🦞 Antigravity Board is running on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
