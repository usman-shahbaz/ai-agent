
"""Streamlit interface for the AI Data Analysis Agent."""

import logging

import pandas as pd
import streamlit as st

from src.config import settings
from src.services.data_loader import DataLoadError, load_dataset
from src.services.query_engine import QueryEngine, QueryError
from src.services.sql_agent import SQLAgent


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="AI Data Analyst",
    page_icon="📊",
    layout="wide",
)

st.title("📊 AI Data Analyst")
st.write(
    "Upload a CSV or Excel file and ask questions about your data "
    "using natural language."
)


def initialize_state() -> None:
    """Initialize session-specific application state."""

    defaults = {
        "dataset_df": None,
        "dataset_name": None,
        "query_history": [],
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def display_dataset(df: pd.DataFrame) -> None:
    """Display a dataset preview and summary metrics."""

    first, second, third = st.columns(3)

    first.metric("Rows", f"{len(df):,}")
    second.metric("Columns", f"{len(df.columns):,}")
    third.metric(
        "Missing cells",
        f"{int(df.isna().sum().sum()):,}",
    )

    with st.expander("Preview dataset", expanded=True):
        st.dataframe(
            df.head(200),
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Column information"):
        schema = pd.DataFrame(
            {
                "Column": [str(column) for column in df.columns],
                "Data type": [str(dtype) for dtype in df.dtypes],
                "Missing values": [
                    int(df[column].isna().sum())
                    for column in df.columns
                ],
            }
        )

        st.dataframe(
            schema,
            use_container_width=True,
            hide_index=True,
        )


def main() -> None:
    """Run the Streamlit application."""

    initialize_state()

    with st.sidebar:
        st.header("Settings")

        api_key = st.text_input(
            "OpenAI API key",
            value=settings.openai_api_key,
            type="password",
            help=(
                "Prefer setting OPENAI_API_KEY in your environment "
                "or .env file."
            ),
        )

        model = st.text_input(
            "Model",
            value=settings.openai_model,
        )

        st.caption(
            f"Maximum file size: {settings.max_upload_mb} MB"
        )
        st.caption(
            f"Maximum data rows: {settings.max_rows:,}"
        )

        if st.button("Clear session data", use_container_width=True):
            st.session_state["dataset_df"] = None
            st.session_state["dataset_name"] = None
            st.session_state["query_history"] = []
            st.rerun()

    uploaded_file = st.file_uploader(
        "Upload your dataset",
        type=["csv", "xlsx", "xls"],
        help="Supported formats: CSV, XLSX, and XLS.",
    )

    # Load a newly selected file. Keep the previous dataset if loading fails.
    if uploaded_file is not None:
        upload_signature = (
            uploaded_file.name,
            uploaded_file.size,
        )

        if st.session_state.get("upload_signature") != upload_signature:
            try:
                with st.spinner("Loading and validating dataset..."):
                    df = load_dataset(
                        uploaded_file,
                        max_upload_mb=settings.max_upload_mb,
                        max_rows=settings.max_rows,
                    )

                st.session_state["dataset_df"] = df
                st.session_state["dataset_name"] = uploaded_file.name
                st.session_state["upload_signature"] = upload_signature
                st.session_state["query_history"] = []

                st.success("Dataset loaded successfully.")

            except DataLoadError as exc:
                st.error(str(exc))
                st.stop()

            except Exception:
                logger.exception("Unexpected dataset loading error.")
                st.error(
                    "An unexpected error occurred while loading "
                    "the dataset."
                )
                st.stop()

    df = st.session_state["dataset_df"]

    if df is None:
        st.info("Upload a dataset to start analyzing your data.")
        st.stop()

    st.subheader(
        f"Dataset: {st.session_state['dataset_name']}"
    )
    display_dataset(df)

    st.divider()
    st.subheader("Ask your data")

    question = st.text_area(
        "What would you like to know?",
        placeholder=(
            "Example: Show the top 10 products by total revenue."
        ),
        max_chars=2000,
        height=100,
    )

    analyze_clicked = st.button(
        "Analyze data",
        type="primary",
        disabled=not bool(api_key.strip() and model.strip()),
    )

    if analyze_clicked:
        if not question.strip():
            st.warning("Please enter a question.")
            st.stop()

        try:
            agent = SQLAgent(
                api_key=api_key,
                model=model,
            )

            engine = QueryEngine(
                df,
                timeout_seconds=settings.query_timeout_seconds,
            )

            with st.spinner("Generating SQL and analyzing data..."):
                sql = agent.generate_sql(question, df)

                # Never execute the model output directly in the UI.
                result = engine.run(sql)

                explanation = agent.explain_result(
                    question,
                    result,
                )

            st.session_state["query_history"].insert(
                0,
                {
                    "question": question,
                    "sql": sql,
                    "rows": len(result),
                },
            )

            st.subheader("Analysis")
            st.write(explanation)

            with st.expander("Inspect generated SQL"):
                st.code(sql, language="sql")

            st.subheader("Query results")
            st.caption(f"{len(result):,} result rows")

            st.dataframe(
                result,
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                label="Download results as CSV",
                data=result.to_csv(index=False).encode("utf-8"),
                file_name="analysis_results.csv",
                mime="text/csv",
            )

        except QueryError as exc:
            st.error(f"Query rejected or failed: {exc}")

        except ValueError as exc:
            st.error(str(exc))

        except Exception:
            logger.exception("Unexpected analysis error.")
            st.error(
                "Analysis failed unexpectedly. Check your API "
                "configuration and application logs."
            )

    if st.session_state["query_history"]:
        with st.expander("Recent query history"):
            for index, item in enumerate(
                st.session_state["query_history"][:10],
                start=1,
            ):
                st.markdown(
                    f"**{index}. {item['question']}**"
                )
                st.caption(
                    f"Result rows: {item['rows']:,}"
                )
                st.code(item["sql"], language="sql")


if __name__ == "__main__":
    main()
