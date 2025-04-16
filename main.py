from graph.graph_config import create_app

if __name__ == "__main__":
    app = create_app()
    result = app.invoke()
    print("✅ Done")