import uvicorn

if __name__ == "__main__":
    print("网络分析器已启动: http://127.0.0.1:8000")
    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, log_level="warning")
