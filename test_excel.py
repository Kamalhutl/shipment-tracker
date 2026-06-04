import pandas as pd

df = pd.read_excel("tracking.xlsx")

print(df.columns)

bl_number = str(df.iloc[0]["BL Number"])

print(bl_number)